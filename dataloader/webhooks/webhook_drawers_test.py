"""Webhook drawer fragment and synthetic test inject (bypasses signature)."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from loguru import logger

from dataloader.routers.deps import CurrentAppUserDep, SettingsDep, TemplatesDep
from dataloader.run_access import user_to_ctx
from dataloader.webhooks.buffer_state import WebhookEntry, _fanout, _webhook_buffer
from dataloader.webhooks.ingest import _render_webhook_html
from dataloader.webhooks.webhook_persist import _buffer_entry_allowed, _persist_webhook
from db.repositories import webhooks as webhooks_repo

router = APIRouter()


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
