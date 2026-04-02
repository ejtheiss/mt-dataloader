"""Webhook receiver, SSE stream, run detail, staged fire, and listener.

Package entrypoint: ``dataloader.webhooks`` re-exports ``router`` and helpers from
``dataloader.webhooks.__init__`` (FastAPI *bigger applications* pattern:
https://fastapi.tiangolo.com/tutorial/bigger-applications/ ).

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
import os
import secrets
import tempfile
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger
from modern_treasury import AsyncModernTreasury
from sse_starlette import EventSourceResponse, ServerSentEvent
from tenacity import RetryError, retry, retry_if_result

from dataloader.routers.deps import SettingsDep, TemplatesDep, TunnelDep
from engine import _now_iso, list_manifest_ids
from handlers import DELETABILITY, TENACITY_STOP_30, TENACITY_WAIT_EXP_2_10
from jsonutil import dumps_jsonl_record, dumps_pretty, loads_path
from models import ManifestEntry, RunManifest
from tunnel import TunnelManager, first_https_tunnel_url

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
    mt_org_id: str | None = None


# ---------------------------------------------------------------------------
# Shared state (module-level, process-lifetime)
# ---------------------------------------------------------------------------

_webhook_buffer: deque[WebhookEntry] = deque(maxlen=500)

_seen_ids_order: deque[str] = deque(maxlen=2000)
_seen_ids: set[str] = set()

_correlation_index: dict[str, tuple[str, str]] = {}

_run_org_map: dict[str, str] = {}

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


def register_run_org(run_id: str, org_id: str) -> None:
    """Record which MT org executed a run (for webhook row org labels / filtering).

    Called from ``engine.execute()`` when a manifest is created so inbound
    webhooks correlated to this run_id can show ``mt_org_id`` before the
    manifest is re-read from disk.
    """
    if run_id and org_id:
        _run_org_map[run_id] = org_id


def ensure_run_indexed(run_id: str, manifest: Any) -> None:
    """Populate the correlation index from a historical manifest.

    Called when loading a run detail page for an older run whose
    resources may not be in memory (e.g. after server restart).
    Indexes both primary IDs and child_refs.
    """
    for entry in manifest.resources_created:
        if entry.created_id not in _correlation_index:
            _correlation_index[entry.created_id] = (run_id, entry.typed_ref)
        for child_key, child_id in entry.child_refs.items():
            if child_id not in _correlation_index:
                _correlation_index[child_id] = (
                    run_id,
                    f"{entry.typed_ref}.{child_key}",
                )


def build_run_org_map(runs_dir: str) -> dict[str, str]:
    """Map run_id → MT org id from manifests (for listener / run detail UI)."""
    out: dict[str, str] = {}
    rpath = Path(runs_dir)
    for run_id in list_manifest_ids(runs_dir):
        try:
            manifest = RunManifest.load(rpath / f"{run_id}.json")
        except Exception:
            continue
        oid = getattr(manifest, "mt_org_id", None)
        if oid:
            out[run_id] = oid
    return out


def enrich_webhooks_run_org(webhooks: list[dict], run_org_map: dict[str, str]) -> None:
    """Fill ``mt_org_id`` on webhook dicts using the run manifest map."""
    for wh in webhooks:
        if wh.get("mt_org_id"):
            continue
        rid = wh.get("run_id")
        if isinstance(rid, str) and rid in run_org_map:
            wh["mt_org_id"] = run_org_map[rid]


def load_webhooks(path: Path) -> list[dict]:
    """Load webhook entries from a JSONL file, newest first."""
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
    entries.reverse()
    return entries


def rebuild_correlation_index(runs_dir: str) -> int:
    """Load all manifests and populate ``_correlation_index``.

    Called once at startup from ``lifespan()`` so that webhooks arriving
    after a server restart can be matched to historical runs.  Also
    re-correlates entries in ``_webhooks_unmatched.jsonl`` — any that now
    match are moved to their run-specific JSONL file.

    Returns the total number of IDs indexed.
    """
    count = 0
    runs_path = Path(runs_dir)
    for run_id in list_manifest_ids(runs_dir):
        try:
            manifest = RunManifest.load(runs_path / f"{run_id}.json")
        except Exception as exc:
            logger.warning("Skipping manifest {} during index rebuild: {}", run_id, exc)
            continue
        oid = getattr(manifest, "mt_org_id", None)
        if oid:
            _run_org_map[run_id] = oid
        for entry in manifest.resources_created:
            _correlation_index[entry.created_id] = (run_id, entry.typed_ref)
            count += 1
            for child_key, child_id in entry.child_refs.items():
                _correlation_index[child_id] = (
                    run_id,
                    f"{entry.typed_ref}.{child_key}",
                )
                count += 1

    recovered = _recorrelate_unmatched(runs_dir)
    if recovered:
        logger.info("Re-correlated {} previously unmatched webhooks", recovered)

    logger.info(
        "Correlation index rebuilt: {} IDs from {} runs",
        count,
        len(list_manifest_ids(runs_dir)),
    )
    return count


def _recorrelate_unmatched(runs_dir: str) -> int:
    """Scan ``_webhooks_unmatched.jsonl`` and move matched entries.

    For each entry whose ``raw.data`` now matches the index, updates
    ``run_id`` and ``typed_ref``, appends to the run-specific JSONL,
    and atomically rewrites the unmatched file without them.
    """
    unmatched_path = Path(runs_dir) / "_webhooks_unmatched.jsonl"
    if not unmatched_path.exists():
        return 0

    entries = load_webhooks(unmatched_path)
    if not entries:
        return 0

    still_unmatched: list[dict] = []
    recovered = 0

    for entry in entries:
        raw = entry.get("raw", {})
        data = raw.get("data", {})
        run_id, typed_ref = _correlate(data)
        if run_id:
            entry["run_id"] = run_id
            entry["typed_ref"] = typed_ref
            run_path = Path(runs_dir) / f"{run_id}_webhooks.jsonl"
            with open(run_path, "a", encoding="utf-8") as f:
                f.write(dumps_jsonl_record(entry) + "\n")
            recovered += 1
        else:
            still_unmatched.append(entry)

    tmp = tempfile.NamedTemporaryFile(dir=runs_dir, mode="w", suffix=".tmp", delete=False)
    try:
        for entry in still_unmatched:
            tmp.write(dumps_jsonl_record(entry) + "\n")
        tmp.close()
        os.replace(tmp.name, str(unmatched_path))
    except Exception:
        tmp.close()
        os.unlink(tmp.name)
        raise

    return recovered


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


_CORRELATION_FIELDS = (
    "internal_account_id",
    "originating_account_id",
    "receiving_account_id",
    "counterparty_id",
    "legal_entity_id",
    "ledger_transaction_id",
    "ledger_account_id",
    "batch_id",
    "returnable_id",
    "virtual_account_id",
    "ledgerable_id",
)


def _correlate(data: dict) -> tuple[str | None, str | None]:
    """Match a webhook payload to a run via the correlation index.

    Checks ``data.id`` first (exact resource match), then scans reference
    fields like ``internal_account_id``, ``originating_account_id``, etc.
    for derivative webhooks (balance reports, transactions, returns).
    """
    if not isinstance(data, dict):
        return None, None
    primary = data.get("id", "")
    if primary and primary in _correlation_index:
        return _correlation_index[primary]
    for field_name in _CORRELATION_FIELDS:
        val = data.get(field_name)
        if isinstance(val, str) and val in _correlation_index:
            return _correlation_index[val]
    return None, None


def _persist_webhook(entry: WebhookEntry, runs_dir: str) -> None:
    """Append webhook entry to the appropriate JSONL file."""
    dirpath = Path(runs_dir)
    dirpath.mkdir(parents=True, exist_ok=True)

    line = (
        dumps_jsonl_record(
            {
                "received_at": entry.received_at,
                "event_type": entry.event_type,
                "resource_type": entry.resource_type,
                "resource_id": entry.resource_id,
                "webhook_id": entry.webhook_id,
                "run_id": entry.run_id,
                "typed_ref": entry.typed_ref,
                "raw": entry.raw,
            }
        )
        + "\n"
    )

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
async def receive_webhook(
    request: Request,
    settings: SettingsDep,
    templates: TemplatesDep,
):
    """Receive and process a Modern Treasury webhook."""

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
                return JSONResponse({"error": "invalid signature"}, status_code=401)
        except Exception as exc:
            logger.warning("Webhook signature verification failed: {}", exc)
            return JSONResponse({"error": "signature verification failed"}, status_code=401)

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
        return JSONResponse({"error": f"malformed payload: {exc}"}, status_code=400)

    if webhook_id and _mark_seen(webhook_id):
        logger.debug("Duplicate webhook {} — skipping", webhook_id)
        return {"ok": True, "duplicate": True}

    run_id, typed_ref = _correlate(data)
    row_org = _run_org_map.get(run_id) if run_id else None

    entry = WebhookEntry(
        received_at=datetime.now(timezone.utc).isoformat(),
        event_type=event_type,
        resource_type=topic,
        resource_id=resource_id,
        webhook_id=webhook_id,
        run_id=run_id,
        typed_ref=typed_ref,
        raw=body,
        mt_org_id=row_org,
    )

    entry.html = _render_webhook_html(entry, templates)

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
async def run_detail_page(
    request: Request,
    run_id: str,
    settings: SettingsDep,
    templates: TemplatesDep,
):
    """Four-tab run detail page: Config, Resources, Staged, Webhooks."""
    runs_dir = Path(settings.runs_dir)

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
            data = loads_path(staged_path)
            if isinstance(data, dict):
                staged_payloads = data
        except (json.JSONDecodeError, OSError, TypeError):
            pass

    webhooks_path = runs_dir / f"{run_id}_webhooks.jsonl"
    webhook_history = load_webhooks(webhooks_path)
    rom = build_run_org_map(str(runs_dir))
    enrich_webhooks_run_org(webhook_history, rom)

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
    client: AsyncModernTreasury,
    resolved: dict,
    *,
    idempotency_key: str,
) -> dict:
    result = await client.payment_orders.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    child_refs: dict[str, str] = {}
    if result.ledger_transaction_id:
        child_refs["ledger_transaction"] = result.ledger_transaction_id
    return {"created_id": result.id, "child_refs": child_refs}


async def _fire_expected_payment(
    client: AsyncModernTreasury,
    resolved: dict,
    *,
    idempotency_key: str,
) -> dict:
    result = await client.expected_payments.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    return {"created_id": result.id}


async def _fire_ledger_transaction(
    client: AsyncModernTreasury,
    resolved: dict,
    *,
    idempotency_key: str,
) -> dict:
    result = await client.ledger_transactions.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    return {"created_id": result.id}


async def _fire_incoming_payment_detail(
    client: AsyncModernTreasury,
    resolved: dict,
    *,
    idempotency_key: str,
) -> dict:
    result = await client.incoming_payment_details.create_async(
        **resolved,
        idempotency_key=idempotency_key,
    )

    @retry(
        wait=TENACITY_WAIT_EXP_2_10,
        stop=TENACITY_STOP_30,
        retry=retry_if_result(lambda r: r.status not in _IPD_TERMINAL),
    )
    async def _poll():
        return await client.incoming_payment_details.retrieve(result.id)

    try:
        ipd = await _poll()
    except RetryError as e:
        last = e.last_attempt.result()
        raise HTTPException(504, f"IPD did not complete within 30s (status: {last.status})")

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

FIREABLE_TYPES = frozenset(_FIRE_DISPATCH.keys())


@router.get("/api/runs/{run_id}/staged/drawer", include_in_schema=False)
async def staged_drawer(
    request: Request,
    run_id: str,
    settings: SettingsDep,
    templates: TemplatesDep,
    ref: str = Query(...),
):
    """Return drawer HTML for a staged resource payload."""
    runs_dir = Path(settings.runs_dir)

    staged_path = runs_dir / f"{run_id}_staged.json"
    if not staged_path.exists():
        raise HTTPException(404, "No staged payloads for this run")

    try:
        raw = loads_path(staged_path)
    except (json.JSONDecodeError, OSError, TypeError) as exc:
        raise HTTPException(404, f"Invalid staged file: {exc}") from exc
    if not isinstance(raw, dict):
        raise HTTPException(404, "Invalid staged file shape")
    staged_payloads: dict[str, dict] = raw
    if ref not in staged_payloads:
        raise HTTPException(404, f"Staged payload not found: {ref}")

    manifest_path = runs_dir / f"{run_id}.json"
    manifest = RunManifest.load(manifest_path) if manifest_path.exists() else None
    staged_at = ""
    if manifest and manifest.resources_staged:
        for s in manifest.resources_staged:
            if s.typed_ref == ref:
                staged_at = s.staged_at
                break

    return templates.TemplateResponse(
        request,
        "partials/staged_drawer.html",
        {"typed_ref": ref, "payload": staged_payloads[ref], "staged_at": staged_at},
    )


@router.post("/api/runs/{run_id}/fire/{typed_ref:path}")
async def fire_staged(
    request: Request,
    run_id: str,
    typed_ref: str,
    settings: SettingsDep,
    templates: TemplatesDep,
    api_key: str = Form(...),
    org_id: str = Form(...),
):
    """Fire a staged resource — sends the resolved payload to the MT API."""
    runs_dir = Path(settings.runs_dir)

    lock = _fire_locks.setdefault(run_id, asyncio.Lock())
    async with lock:
        staged_path = runs_dir / f"{run_id}_staged.json"
        if not staged_path.exists():
            raise HTTPException(404, "No staged payloads for this run")

        try:
            loaded = loads_path(staged_path)
        except (json.JSONDecodeError, OSError, TypeError) as exc:
            raise HTTPException(404, f"Invalid staged file: {exc}") from exc
        if not isinstance(loaded, dict):
            raise HTTPException(404, "Invalid staged file shape")
        staged_payloads = loaded
        if typed_ref not in staged_payloads:
            raise HTTPException(404, f"Staged payload not found: {typed_ref}")

        resolved = staged_payloads[typed_ref]
        resource_type = typed_ref.split(".")[0]

        handler = _FIRE_DISPATCH.get(resource_type)
        if not handler:
            supported = ", ".join(sorted(FIREABLE_TYPES))
            raise HTTPException(
                400,
                f"Cannot fire staged '{resource_type}'. Fireable types: {supported}",
            )

        async with AsyncModernTreasury(api_key=api_key, organization_id=org_id) as client:
            result = await handler(
                client,
                resolved,
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
                child_refs=result.get("child_refs", {}),
            )
        )
        manifest.resources_staged = [
            s for s in manifest.resources_staged if s.typed_ref != typed_ref
        ]
        manifest.write(settings.runs_dir)

        del staged_payloads[typed_ref]
        if staged_payloads:
            staged_path.write_text(dumps_pretty(staged_payloads), encoding="utf-8")
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


def _detect_tunnel_from_manager(mgr: TunnelManager | None) -> str | None:
    """Check TunnelManager (pyngrok-managed) for an active tunnel URL."""
    if mgr is None:
        return None
    status = mgr.get_status()
    if status.get("connected") and status.get("url"):
        return status["url"]
    return None


async def _detect_tunnel_legacy() -> str | None:
    """Probe ngrok local API for a public tunnel URL (external ngrok)."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as http:
            resp = await http.get("http://127.0.0.1:4040/api/tunnels")
            if resp.status_code == 200:
                data = resp.json()
                url = first_https_tunnel_url(data)
                if url:
                    return url
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        pass
    return None


async def _detect_tunnel(mgr: TunnelManager | None) -> str | None:
    """Try TunnelManager first, fall back to probing external ngrok."""
    url = _detect_tunnel_from_manager(mgr)
    if url:
        return url
    return await _detect_tunnel_legacy()


@router.get("/listen", include_in_schema=False)
async def listen_page(
    request: Request,
    settings: SettingsDep,
    templates: TemplatesDep,
    tunnel_mgr: TunnelDep,
    run_id: str | None = None,
):
    """Standalone webhook listener with tunnel auto-detection and run filter."""
    tunnel_url = await _detect_tunnel(tunnel_mgr)

    mgr = tunnel_mgr
    saved_authtoken = ""
    saved_domain = ""
    saved_webhook_endpoint_id = ""
    if mgr:
        saved_authtoken = settings.ngrok_authtoken or mgr.saved_authtoken
        saved_domain = settings.ngrok_domain or mgr.saved_domain
        saved_webhook_endpoint_id = mgr.saved_webhook_endpoint_id

    tunnel_setup_collapsed = bool(tunnel_url and saved_webhook_endpoint_id)

    webhook_history: list[dict] = []
    run_org_map = build_run_org_map(settings.runs_dir)
    if run_id:
        webhooks_path = Path(settings.runs_dir) / f"{run_id}_webhooks.jsonl"
        webhook_history = load_webhooks(webhooks_path)
        enrich_webhooks_run_org(webhook_history, run_org_map)

    run_ids = list_manifest_ids(settings.runs_dir)

    return templates.TemplateResponse(
        request,
        "listen.html",
        {
            "tunnel_url": tunnel_url,
            "webhook_path": "/webhooks/mt",
            "webhook_history": webhook_history,
            "run_ids": run_ids,
            "run_org_map": run_org_map,
            "selected_run_id": run_id,
            "saved_authtoken": saved_authtoken,
            "saved_domain": saved_domain,
            "saved_webhook_endpoint_id": saved_webhook_endpoint_id,
            "tunnel_setup_collapsed": tunnel_setup_collapsed,
        },
    )


@router.get("/api/webhooks/{webhook_id}/drawer", include_in_schema=False)
async def webhook_drawer(
    request: Request,
    webhook_id: str,
    settings: SettingsDep,
    templates: TemplatesDep,
):
    """Return drawer HTML for a single webhook by ID."""
    for entry in reversed(_webhook_buffer):
        if entry.webhook_id == webhook_id:
            return templates.TemplateResponse(
                request,
                "partials/webhook_detail_drawer.html",
                {"wh": entry},
            )

    runs_dir = Path(settings.runs_dir)
    for jsonl_path in runs_dir.glob("*_webhooks.jsonl"):
        for wh_dict in load_webhooks(jsonl_path):
            if wh_dict.get("webhook_id") == webhook_id:
                return templates.TemplateResponse(
                    request,
                    "partials/webhook_detail_drawer.html",
                    {"wh": wh_dict},
                )
    unmatched = runs_dir / "_webhooks_unmatched.jsonl"
    for wh_dict in load_webhooks(unmatched):
        if wh_dict.get("webhook_id") == webhook_id:
            return templates.TemplateResponse(
                request,
                "partials/webhook_detail_drawer.html",
                {"wh": wh_dict},
            )

    return templates.TemplateResponse(
        request,
        "partials/empty_state.html",
        {
            "empty_title": "Webhook not found",
            "empty_description": f"No data for webhook {webhook_id[:16]}…",
        },
        status_code=404,
    )


@router.post("/api/webhooks/test", include_in_schema=False)
async def send_test_webhook(
    request: Request,
    settings: SettingsDep,
    templates: TemplatesDep,
):
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
    entry.html = _render_webhook_html(entry, templates)

    _persist_webhook(entry, settings.runs_dir)
    _webhook_buffer.append(entry)
    await _fanout(entry)
    return {"ok": True}
