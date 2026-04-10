"""POST ``/webhooks/mt`` — inbound Modern Treasury webhook receiver."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from loguru import logger

from dataloader.routers.deps import SettingsDep, TemplatesDep
from dataloader.webhooks import correlation_state
from dataloader.webhooks.buffer_state import (
    WebhookEntry,
    _fanout,
    _get_sig_client,
    _mark_seen,
    _webhook_buffer,
)
from dataloader.webhooks.webhook_persist import _persist_webhook

router = APIRouter()


def _correlate(data: dict) -> tuple[str | None, str | None]:
    """Match a webhook payload to a run via the process correlation index (tests)."""
    return correlation_state.correlate_inbound_payload(data)


def _render_webhook_html(entry: WebhookEntry, templates: Any) -> str:
    """Render a webhook entry as an HTML snippet using the Jinja2 partial."""
    return templates.get_template("partials/webhook_row.html").render(wh=entry)


@router.post("/webhooks/mt", tags=["agent"])
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
