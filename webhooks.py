"""Webhook receiver, SSE stream, and correlation for MT Dataloader.

Provides:
- POST /webhooks/mt        — inbound webhook receiver
- GET  /webhooks/stream    — SSE fan-out for live webhook events
- Correlation index mapping MT resource IDs to run_id + typed_ref
- In-memory ring buffer for recent webhooks
- JSONL persistence per run
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger
from modern_treasury import AsyncModernTreasury
from sse_starlette import EventSourceResponse, ServerSentEvent

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


# ---------------------------------------------------------------------------
# Shared state (module-level, process-lifetime)
# ---------------------------------------------------------------------------

_webhook_buffer: deque[WebhookEntry] = deque(maxlen=500)

_seen_ids_order: deque[str] = deque(maxlen=2000)
_seen_ids: set[str] = set()

_correlation_index: dict[str, tuple[str, str]] = {}

_webhook_listeners: list[tuple[str | None, asyncio.Queue]] = []

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


def _render_webhook_row(entry: WebhookEntry) -> str:
    """Render a webhook entry as an HTML snippet for SSE push.

    Temporary inline rendering — Step 3 replaces with Jinja2 template.
    """
    time_str = (
        entry.received_at.split("T")[1][:8]
        if "T" in entry.received_at
        else entry.received_at
    )
    ref_display = entry.typed_ref or f"{entry.resource_id[:12]}..."
    return (
        f'<div class="webhook-row" id="wh-{entry.webhook_id}">'
        f'<span class="wh-time">{time_str}</span> '
        f'<span class="wh-event">{entry.event_type}</span> '
        f'<span class="wh-ref">{ref_display}</span>'
        f"</div>"
    )


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
                        html = _render_webhook_row(entry)
                        yield ServerSentEvent(data=html, event="webhook")

            while True:
                entry = await q.get()
                html = _render_webhook_row(entry)
                yield ServerSentEvent(data=html, event="webhook")

        except asyncio.CancelledError:
            pass
        finally:
            try:
                _webhook_listeners.remove(listener)
            except ValueError:
                pass

    return EventSourceResponse(event_generator(), ping=15)
