"""Webhook receiver, SSE stream, run detail, staged fire, and listener.

Package entrypoint: ``dataloader.webhooks`` re-exports ``router`` and helpers from
``dataloader.webhooks.__init__`` (FastAPI *bigger applications* pattern:
https://fastapi.tiangolo.com/tutorial/bigger-applications/ ).

Provides:
- POST /webhooks/mt           — inbound webhook receiver
- GET  /webhooks/stream       — SSE fan-out (**admin:** all runs; **user:** ``run_id`` required + readable)
- GET  /runs/{run_id}         — four-tab run detail page
- POST /api/runs/{run_id}/fire/{typed_ref} — fire a staged resource
- GET  /listen                — standalone webhook listener with tunnel detection
- POST /api/webhooks/test     — inject synthetic test webhook (bypasses sig check)
- Correlation index mapping MT resource IDs to run_id + typed_ref
- In-memory ring buffer for recent webhooks
- SQLite ``webhook_events`` for persistence and history
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
from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from loguru import logger
from modern_treasury import AsyncModernTreasury
from sse_starlette import EventSourceResponse, ServerSentEvent
from tenacity import RetryError, retry, retry_if_result

from dataloader.engine import _now_iso
from dataloader.handlers import DELETABILITY, TENACITY_STOP_30, TENACITY_WAIT_EXP_2_10
from dataloader.routers.deps import CurrentAppUserDep, SettingsDep, TemplatesDep, TunnelDep
from dataloader.run_access import load_run_manifest_for_reader, run_is_readable, user_to_ctx
from dataloader.staged_fire import FIREABLE_TYPES
from dataloader.tunnel import TunnelManager, first_https_tunnel_url
from dataloader.webhooks import correlation_state
from db.repositories import runs as runs_repo
from db.repositories import webhooks as webhooks_repo
from jsonutil import dumps_pretty, loads_path
from models import AppSettings, CurrentAppUser, ManifestEntry, RunManifest

router = APIRouter()

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class WebhookEntry:
    """Single received webhook, stored in memory and in ``webhook_events``."""

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

_webhook_listeners: list[tuple[str | None, asyncio.Queue[WebhookEntry]]] = []

_sig_client: AsyncModernTreasury | None = None

# Correlation index + run→org maps: ``dataloader.webhooks.correlation_state``


def enrich_webhooks_run_org(webhooks: list[dict], run_org_map: dict[str, str]) -> None:
    """Fill ``mt_org_id`` on webhook dicts when missing (map from ``runs.mt_org_id``)."""
    for wh in webhooks:
        if wh.get("mt_org_id"):
            continue
        rid = wh.get("run_id")
        if isinstance(rid, str) and rid in run_org_map:
            wh["mt_org_id"] = run_org_map[rid]


async def _persist_webhook(request: Request, entry: WebhookEntry) -> None:
    """Insert into ``webhook_events``."""
    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is None:
        return
    raw = entry.raw if isinstance(entry.raw, dict) else {}
    try:
        async with factory() as session:
            await webhooks_repo.insert_webhook_event(
                session,
                webhook_id=entry.webhook_id or None,
                run_id=entry.run_id,
                typed_ref=entry.typed_ref,
                received_at=entry.received_at,
                event_type=entry.event_type,
                resource_type=entry.resource_type,
                resource_id=entry.resource_id,
                raw=raw,
            )
            await session.commit()
    except Exception as exc:
        logger.bind(run_id=entry.run_id or "unmatched").warning(
            "webhook DB persist failed: {}",
            exc,
        )


async def _load_webhook_history_for_run(
    request: Request,
    settings: AppSettings,
    run_id: str,
    user: CurrentAppUser,
) -> list[dict]:
    """List webhook history for a run from ``webhook_events`` (empty if unavailable)."""
    ctx = user_to_ctx(user)
    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is None:
        return []
    try:
        async with factory() as session:
            return await webhooks_repo.list_webhook_history_dicts_for_run(session, run_id, ctx)
    except Exception as exc:
        logger.bind(run_id=run_id).warning("webhook DB list failed: {}", exc)
        return []


async def _webhook_history_and_org_for_run_detail(
    request: Request,
    run_id: str,
    user: CurrentAppUser,
) -> tuple[list[dict], dict[str, str]]:
    """One session: webhook history + org map for the webhooks tab."""
    ctx = user_to_ctx(user)
    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is None:
        return [], {}
    try:
        async with factory() as session:
            history = await webhooks_repo.list_webhook_history_dicts_for_run(session, run_id, ctx)
            row = await runs_repo.get_run_row_for_access(session, run_id, ctx)
        rom = {run_id: row.mt_org_id} if row and row.mt_org_id else {}
        return history, rom
    except Exception as exc:
        logger.bind(run_id=run_id).warning("run detail: webhook history/org failed: {}", exc)
        return [], {}


async def _buffer_entry_allowed(
    request: Request, entry: WebhookEntry, user: CurrentAppUser
) -> bool:
    """Hide in-memory buffer rows from callers who cannot read the parent run."""
    ctx = user_to_ctx(user)
    if entry.run_id is None:
        return ctx.is_admin
    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is None:
        return True
    try:
        async with factory() as session:
            row = await runs_repo.get_run_row_for_access(session, entry.run_id, ctx)
        return row is not None
    except Exception:
        return False


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


def _correlate(data: dict) -> tuple[str | None, str | None]:
    """Match a webhook payload to a run via the process correlation index (tests)."""
    return correlation_state.correlate_inbound_payload(data)


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
    row_org = correlation_state.mt_org_for_run(run_id)

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

    await _persist_webhook(request, entry)
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
    settings: SettingsDep,
    current_user: CurrentAppUserDep,
    run_id: str | None = None,
    no_replay: bool = False,
):
    """SSE stream of incoming webhooks.

    **Admin** may omit ``run_id`` to receive all runs. **User** must pass a
    ``run_id`` they are allowed to read (same rule as run detail); otherwise
    ``404`` / ``403`` as appropriate.

    Pass ``no_replay=true`` to skip ring-buffer replay (used by the run
    detail page where historical webhooks are already server-rendered).
    """
    rid = (run_id or "").strip()
    if not current_user.is_admin:
        if not rid:
            raise HTTPException(
                status_code=403,
                detail="Non-admin clients must pass run_id to subscribe to /webhooks/stream",
            )
        if not await run_is_readable(request, settings, rid, current_user):
            raise HTTPException(status_code=404, detail="Run not found")

    async def event_generator():
        q: asyncio.Queue[WebhookEntry] = asyncio.Queue(maxsize=100)
        filter_run_id = rid if rid else None
        listener = (filter_run_id, q)
        _webhook_listeners.append(listener)

        try:
            if not no_replay:
                for entry in list(_webhook_buffer):
                    if filter_run_id is None or entry.run_id == filter_run_id:
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
    current_user: CurrentAppUserDep,
):
    """Four-tab run detail page: Config, Resources, Staged, Webhooks."""
    runs_dir = Path(settings.runs_dir)

    manifest = await load_run_manifest_for_reader(request, settings, run_id, current_user)
    if manifest is None:
        raise HTTPException(404, f"Run '{run_id}' not found")
    correlation_state.ensure_run_indexed(run_id, manifest)

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

    webhook_history, rom = await _webhook_history_and_org_for_run_detail(
        request, run_id, current_user
    )
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

if frozenset(_FIRE_DISPATCH) != FIREABLE_TYPES:
    raise RuntimeError("_FIRE_DISPATCH keys must match dataloader.staged_fire.FIREABLE_TYPES")


@router.get("/api/runs/{run_id}/staged/drawer", include_in_schema=False)
async def staged_drawer(
    request: Request,
    run_id: str,
    settings: SettingsDep,
    templates: TemplatesDep,
    current_user: CurrentAppUserDep,
    ref: str = Query(...),
):
    """Return drawer HTML for a staged resource payload."""
    if not await run_is_readable(request, settings, run_id, current_user):
        raise HTTPException(404, "Run not found")

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
    current_user: CurrentAppUserDep,
    api_key: str = Form(...),
    org_id: str = Form(...),
):
    """Fire a staged resource — sends the resolved payload to the MT API."""
    if not await run_is_readable(request, settings, run_id, current_user):
        raise HTTPException(404, "Run not found")

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

    correlation_state.index_resource(run_id, result["created_id"], typed_ref)
    for child_key, child_id in result.get("child_refs", {}).items():
        correlation_state.index_resource(run_id, child_id, f"{typed_ref}.{child_key}")

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
    current_user: CurrentAppUserDep,
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
    run_ids: list[str] = []
    run_org_map: dict[str, str] = {}
    listen_run_list_ok = False
    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is not None:
        try:
            async with factory() as session:
                rows = await runs_repo.list_run_rows_for_api(session, user_to_ctx(current_user))
            run_ids = [r.run_id for r in rows]
            for r in rows:
                if r.mt_org_id:
                    run_org_map[r.run_id] = r.mt_org_id
            listen_run_list_ok = True
        except Exception as exc:
            logger.warning("listen page: DB run list failed: {}", exc)
    if run_id:
        if await run_is_readable(request, settings, run_id, current_user):
            webhook_history = await _load_webhook_history_for_run(
                request, settings, run_id, current_user
            )
            enrich_webhooks_run_org(webhook_history, run_org_map)

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
            "webhook_stream_requires_run_filter": not current_user.is_admin,
            "listen_run_list_ok": listen_run_list_ok,
        },
    )


@router.get("/api/webhooks/{webhook_id}/drawer", include_in_schema=False)
async def webhook_drawer(
    request: Request,
    webhook_id: str,
    settings: SettingsDep,
    templates: TemplatesDep,
    current_user: CurrentAppUserDep,
):
    """Return drawer HTML for a single webhook by ID (MT id or synthetic ``db-{pk}``)."""
    for entry in reversed(_webhook_buffer):
        if entry.webhook_id == webhook_id and await _buffer_entry_allowed(
            request, entry, current_user
        ):
            return templates.TemplateResponse(
                request,
                "partials/webhook_detail_drawer.html",
                {"wh": entry},
            )

    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is not None:
        try:
            async with factory() as session:
                d = await webhooks_repo.get_webhook_history_dict_for_reader(
                    session,
                    webhook_id,
                    user_to_ctx(current_user),
                )
            if d:
                return templates.TemplateResponse(
                    request,
                    "partials/webhook_detail_drawer.html",
                    {"wh": d},
                )
        except Exception as exc:
            logger.warning("webhook drawer DB lookup failed: {}", exc)

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

    await _persist_webhook(request, entry)
    _webhook_buffer.append(entry)
    await _fanout(entry)
    return {"ok": True}
