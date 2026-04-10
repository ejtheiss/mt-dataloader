"""SQLite ``webhook_events`` persistence and history helpers shared across webhook routes."""

from __future__ import annotations

from fastapi import Request
from loguru import logger

from dataloader.run_access import user_to_ctx
from dataloader.webhooks.buffer_state import WebhookEntry
from db.repositories import runs as runs_repo
from db.repositories import webhooks as webhooks_repo
from models import AppSettings, CurrentAppUser


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
