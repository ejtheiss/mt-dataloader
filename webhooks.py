"""Webhook receiver, SSE stream, run detail, staged fire, and listener.

Provides:
- POST /webhooks/mt           — inbound webhook receiver
- GET  /webhooks/stream       — SSE fan-out for live webhook events
- GET  /runs/{run_id}         — four-tab run detail page
- POST /api/runs/{run_id}/fire/{typed_ref} — fire a staged resource
- GET  /listen                — standalone webhook listener with tunnel detection
- POST /api/webhooks/test     — inject synthetic test webhook (bypasses sig check)
- Correlation index mapping MT resource IDs to run_id + typed_ref
- In-memory ring buffer for recent webhooks
- JSONL persistence per run
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger
from modern_treasury import AsyncModernTreasury
from sse_starlette import EventSourceResponse, ServerSentEvent
from tenacity import RetryError, retry, retry_if_result, stop_after_delay, wait_exponential

from engine import RunManifest, _now_iso
from handlers import DELETABILITY
from models import ManifestEntry

router = APIRouter()

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class WebhookEntry:
    """Single received webhook, stored in memory and on disk."""

    received_at: str
    event_type: str
    resource_type: str
    resource_id: str
    webhook_id: str
    run_id: str | None
    typed_ref: str | None
    raw: dict
    html: str = ""


# ---------------------------------------------------------------------------
# Shared state (module-level, process-lifetime)
# ---------------------------------------------------------------------------

_webhook_buffer: deque[WebhookEntry] = deque(maxlen=500)

_seen_ids_order: deque[str] = deque(maxlen=2000)
_seen_ids: set[str] = set()

_correlation_index: dict[str, tuple[str, str]] = {}

_webhook_listeners: list[tuple[str | None, asyncio.Queue[WebhookEntry]]] = []

_sig_client: AsyncModernTreasury | None = None

# ---------------------------------------------------------------------------
# Public interface (called from main.py)
# ---------------------------------------------------------------------------


def index_resource(run_id: str, created_id: str, typed_ref: str) -> None:
    """Register a created resource for webhook correlation.

    Called via the ``on_resource_created`` callback wired into
    ``engine.execute()``, immediately after the handler returns.
    """
    _correlation_index[created_id] = (run_id, typed_ref)


def ensure_run_indexed(run_id: str, manifest: Any) -> None:
    """Populate the correlation index from a historical manifest.

    Called when loading a run detail page for an older run whose
    resources may not be in memory (e.g. after server restart).
    """
    for entry in manifest.resources_created:
        if entry.created_id not in _correlation_index:
            _correlation_index[entry.created_id] = (run_id, entry.typed_ref)


def load_webhooks(path: Path) -> list[dict]:
    """Load webhook entries from a JSONL file."""
    entries: list[dict] = []
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return entries


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _mark_seen(webhook_id: str) -> bool:
    """Track a webhook ID for dedup.  Returns True if already seen."""
    if webhook_id in _seen_ids:
        return True
    if len(_seen_ids_order) == _seen_ids_order.maxlen:
        evicted = _seen_ids_order[0]
        _seen_ids.discard(evicted)
    _seen_ids_order.append(webhook_id)
    _seen_ids.add(webhook_id)
    return False


def _get_sig_client(secret: str) -> AsyncModernTreasury:
    """Lazy singleton for webhook signature verification."""
    global _sig_client
    if _sig_client is None:
        _sig_client = AsyncModernTreasury(
            api_key="unused",
            organization_id="unused",
            webhook_key=secret,
        )
    return _sig_client


def _correlate(resource_id: str) -> tuple[str | None, str | None]:
    """Look up a resource_id in the correlation index."""
    if resource_id in _correlation_index:
        return _correlation_index[resource_id]
    return None, None


def _persist_webhook(entry: WebhookEntry, runs_dir: str) -> None:
    """Append webhook entry to the appropriate JSONL file."""
    dirpath = Path(runs_dir)
    dirpath.mkdir(parents=True, exist_ok=True)

    line = json.dumps({
        "received_at": entry.received_at,
        "event_type": entry.event_type,
        "resource_type": entry.resource_type,
        "resource_id": entry.resource_id,
        "webhook_id": entry.webhook_id,
        "run_id": entry.run_id,
        "typed_ref": entry.typed_ref,
        "raw": entry.raw,
    }, default=str) + "\n"

    if entry.run_id:
        path = dirpath / f"{entry.run_id}_webhooks.jsonl"
    else:
        path = dirpath / "_webhooks_unmatched.jsonl"

    with open(path, "a", encoding="utf-8") as f:
        f.write(line)


async def _fanout(entry: WebhookEntry) -> None:
    """Push a webhook entry to all connected SSE listeners."""
    dead: list[int] = []
    for i, (filter_run_id, q) in enumerate(_webhook_listeners):
        if filter_run_id is None or filter_run_id == entry.run_id:
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                dead.append(i)
    for i in reversed(dead):
        _webhook_listeners.pop(i)


def _render_webhook_html(entry: WebhookEntry, templates: Any) -> str:
    """Render a webhook entry as an HTML snippet using the Jinja2 partial."""
    return templates.get_template("partials/webhook_row.html").render(wh=entry)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("/webhooks/mt")
async def receive_webhook(request: Request):
    """Receive and process a Modern Treasury webhook."""
    settings = request.app.state.settings

    raw_bytes = await request.body()
    raw_str = raw_bytes.decode("utf-8")

    if settings.webhook_secret:
        try:
            client = _get_sig_client(settings.webhook_secret)
            is_valid = client.webhooks.validate_signature(
                payload=raw_str,
                headers=dict(request.headers),
            )
            if not is_valid:
                logger.warning("Webhook signature mismatch — rejecting")
                return JSONResponse(
                    {"error": "invalid signature"}, status_code=401
                )
        except Exception as exc:
            logger.warning("Webhook signature verification failed: {}", exc)
            return JSONResponse(
                {"error": "signature verification failed"}, status_code=401
            )

    try:
        body = json.loads(raw_str)
        if not isinstance(body, dict):
            raise ValueError("payload is not a JSON object")
        topic = request.headers.get("X-Topic", "unknown")
        event = body.get("event", "unknown")
        event_type = f"{topic}.{event}"
        data = body.get("data") or {}
        resource_id = data.get("id", "") if isinstance(data, dict) else ""
        webhook_id = request.headers.get("X-Webhook-ID", "")
    except (json.JSONDecodeError, ValueError, AttributeError) as exc:
        logger.warning("Malformed webhook payload: {}", exc)
        return JSONResponse(
            {"error": f"malformed payload: {exc}"}, status_code=400
        )

    if webhook_id and _mark_seen(webhook_id):
        logger.debug("Duplicate webhook {} — skipping", webhook_id)
        return {"ok": True, "duplicate": True}

    run_id, typed_ref = _correlate(resource_id)

    entry = WebhookEntry(
        received_at=datetime.now(timezone.utc).isoformat(),
        event_type=event_type,
        resource_type=topic,
        resource_id=resource_id,
        webhook_id=webhook_id,
        run_id=run_id,
        typed_ref=typed_ref,
        raw=body,
    )

    entry.html = _render_webhook_html(entry, request.app.state.templates)

    _persist_webhook(entry, settings.runs_dir)
    _webhook_buffer.append(entry)
    await _fanout(entry)

    logger.bind(
        event_type=event_type,
        resource_id=resource_id[:12] if resource_id else "?",
        run_id=run_id or "unmatched",
    ).info("Webhook received")

    return {"ok": True}


@router.get("/webhooks/stream")
async def webhook_stream(
    request: Request,
    run_id: str | None = None,
    no_replay: bool = False,
):
    """SSE stream of incoming webhooks.  Optionally filtered by run_id.

    Pass ``no_replay=true`` to skip ring-buffer replay (used by the run
    detail page where historical webhooks are already server-rendered).
    """

    async def event_generator():
        q: asyncio.Queue[WebhookEntry] = asyncio.Queue(maxsize=100)
        listener = (run_id, q)
        _webhook_listeners.append(listener)

        try:
            if not no_replay:
                for entry in list(_webhook_buffer):
                    if run_id is None or entry.run_id == run_id:
                        yield ServerSentEvent(data=entry.html, event="webhook")

            while True:
                entry = await q.get()
                yield ServerSentEvent(data=entry.html, event="webhook")

        except asyncio.CancelledError:
            pass
        finally:
            try:
                _webhook_listeners.remove(listener)
            except ValueError:
                pass

    return EventSourceResponse(event_generator(), ping=15)


# ---------------------------------------------------------------------------
# Run detail page
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}", include_in_schema=False)
async def run_detail_page(request: Request, run_id: str):
    """Four-tab run detail page: Config, Resources, Staged, Webhooks."""
    settings = request.app.state.settings
    runs_dir = Path(settings.runs_dir)
    templates = request.app.state.templates

    manifest_path = runs_dir / f"{run_id}.json"
    if not manifest_path.exists():
        raise HTTPException(404, f"Run '{run_id}' not found")

    manifest = RunManifest.load(manifest_path)
    ensure_run_indexed(run_id, manifest)

    config_path = runs_dir / f"{run_id}_config.json"
    config_json = config_path.read_text("utf-8") if config_path.exists() else "{}"

    staged_path = runs_dir / f"{run_id}_staged.json"
    staged_payloads: dict[str, dict] = {}
    if staged_path.exists():
        try:
            staged_payloads = json.loads(staged_path.read_text("utf-8"))
        except json.JSONDecodeError:
            pass

    webhooks_path = runs_dir / f"{run_id}_webhooks.jsonl"
    webhook_history = load_webhooks(webhooks_path)

    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {
            "run_id": run_id,
            "manifest": manifest,
            "config_json": config_json,
            "staged_payloads": staged_payloads,
            "webhook_history": webhook_history,
        },
    )


# ---------------------------------------------------------------------------
# Fire staged resources
# ---------------------------------------------------------------------------

_IPD_TERMINAL = {"completed", "returned", "failed"}

_fire_locks: dict[str, asyncio.Lock] = {}


async def _fire_payment_order(
    client: AsyncModernTreasury, resolved: dict, *, idempotency_key: str,
) -> dict:
    result = await client.payment_orders.create(
        **resolved, idempotency_key=idempotency_key,
    )
    return {"created_id": result.id}


async def _fire_expected_payment(
    client: AsyncModernTreasury, resolved: dict, *, idempotency_key: str,
) -> dict:
    result = await client.expected_payments.create(
        **resolved, idempotency_key=idempotency_key,
    )
    return {"created_id": result.id}


async def _fire_ledger_transaction(
    client: AsyncModernTreasury, resolved: dict, *, idempotency_key: str,
) -> dict:
    result = await client.ledger_transactions.create(
        **resolved, idempotency_key=idempotency_key,
    )
    return {"created_id": result.id}


async def _fire_incoming_payment_detail(
    client: AsyncModernTreasury, resolved: dict, *, idempotency_key: str,
) -> dict:
    result = await client.incoming_payment_details.create_async(
        **resolved, idempotency_key=idempotency_key,
    )

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_delay(30),
        retry=retry_if_result(lambda r: r.status not in _IPD_TERMINAL),
    )
    async def _poll():
        return await client.incoming_payment_details.retrieve(result.id)

    try:
        ipd = await _poll()
    except RetryError as e:
        last = e.last_attempt.result()
        raise HTTPException(
            504, f"IPD did not complete within 30s (status: {last.status})"
        )

    if ipd.status != "completed":
        raise HTTPException(
            502,
            f"IPD reached terminal state '{ipd.status}', not 'completed'",
        )

    child_refs: dict[str, str] = {}
    if ipd.transaction_id:
        child_refs["transaction"] = ipd.transaction_id
    if ipd.ledger_transaction_id:
        child_refs["ledger_transaction"] = ipd.ledger_transaction_id

    return {"created_id": result.id, "child_refs": child_refs}


_FIRE_DISPATCH = {
    "payment_order": _fire_payment_order,
    "expected_payment": _fire_expected_payment,
    "ledger_transaction": _fire_ledger_transaction,
    "incoming_payment_detail": _fire_incoming_payment_detail,
}


@router.post("/api/runs/{run_id}/fire/{typed_ref:path}")
async def fire_staged(
    request: Request,
    run_id: str,
    typed_ref: str,
    api_key: str = Form(...),
    org_id: str = Form(...),
):
    """Fire a staged resource — sends the resolved payload to the MT API."""
    settings = request.app.state.settings
    runs_dir = Path(settings.runs_dir)
    templates = request.app.state.templates

    lock = _fire_locks.setdefault(run_id, asyncio.Lock())
    async with lock:
        staged_path = runs_dir / f"{run_id}_staged.json"
        if not staged_path.exists():
            raise HTTPException(404, "No staged payloads for this run")

        staged_payloads = json.loads(staged_path.read_text("utf-8"))
        if typed_ref not in staged_payloads:
            raise HTTPException(404, f"Staged payload not found: {typed_ref}")

        resolved = staged_payloads[typed_ref]
        resource_type = typed_ref.split(".")[0]

        handler = _FIRE_DISPATCH.get(resource_type)
        if not handler:
            raise HTTPException(400, f"Unsupported staged type: {resource_type}")

        async with AsyncModernTreasury(
            api_key=api_key, organization_id=org_id
        ) as client:
            result = await handler(
                client, resolved,
                idempotency_key=f"{run_id}:staged:{typed_ref}",
            )

        manifest_path = runs_dir / f"{run_id}.json"
        manifest = RunManifest.load(manifest_path)
        manifest.resources_created.append(
            ManifestEntry(
                batch=-1,
                resource_type=resource_type,
                typed_ref=typed_ref,
                created_id=result["created_id"],
                created_at=_now_iso(),
                deletable=DELETABILITY.get(resource_type, False),
            )
        )
        manifest.resources_staged = [
            s for s in manifest.resources_staged if s.typed_ref != typed_ref
        ]
        manifest.write(settings.runs_dir)

        del staged_payloads[typed_ref]
        if staged_payloads:
            staged_path.write_text(
                json.dumps(staged_payloads, indent=2, default=str), "utf-8"
            )
        else:
            staged_path.unlink(missing_ok=True)

    index_resource(run_id, result["created_id"], typed_ref)
    for child_key, child_id in result.get("child_refs", {}).items():
        index_resource(run_id, child_id, f"{typed_ref}.{child_key}")

    html = templates.get_template("partials/staged_row_fired.html").render(
        s_typed_ref=typed_ref,
        resource_type=resource_type,
        created_id=result["created_id"],
        child_refs=result.get("child_refs", {}),
    )
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# Standalone listener + tunnel detection
# ---------------------------------------------------------------------------


async def _detect_tunnel() -> str | None:
    """Probe ngrok local API for a public tunnel URL."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as http:
            resp = await http.get("http://127.0.0.1:4040/api/tunnels")
            if resp.status_code == 200:
                data = resp.json()
                for tunnel in data.get("tunnels", []):
                    public_url = tunnel.get("public_url", "")
                    if public_url.startswith("https://"):
                        return public_url
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        pass
    return None


@router.get("/listen", include_in_schema=False)
async def listen_page(request: Request):
    """Standalone webhook listener with tunnel auto-detection."""
    tunnel_url = await _detect_tunnel()
    templates = request.app.state.templates

    return templates.TemplateResponse(
        request,
        "listen.html",
        {
            "tunnel_url": tunnel_url,
            "webhook_path": "/webhooks/mt",
        },
    )


@router.post("/api/webhooks/test", include_in_schema=False)
async def send_test_webhook(request: Request):
    """Inject a synthetic webhook for testing — bypasses signature verification."""
    entry = WebhookEntry(
        received_at=datetime.now(timezone.utc).isoformat(),
        event_type="test.manual",
        resource_type="test",
        resource_id=f"test-{secrets.token_hex(4)}",
        webhook_id=f"test-{int(datetime.now(timezone.utc).timestamp() * 1000)}",
        run_id=None,
        typed_ref=None,
        raw={"event": "test", "data": {"id": "test-manual", "object": "test"}},
    )
    entry.html = _render_webhook_html(entry, request.app.state.templates)

    _persist_webhook(entry, request.app.state.settings.runs_dir)
    _webhook_buffer.append(entry)
    await _fanout(entry)
    return {"ok": True}
