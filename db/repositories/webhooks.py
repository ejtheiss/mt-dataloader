"""Inbound webhook log — Wave C (SQLite ``webhook_events``)."""

from __future__ import annotations

import json
import re

from sqlalchemy import insert, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.repositories.runs import RunAccessContext, get_run_row_for_access
from db.tables import WebhookEvent

_DB_ID_RE = re.compile(r"^db-(\d+)$")


def webhook_public_id(row: WebhookEvent) -> str:
    """Stable id for URLs/templates: MT ``webhook_id`` or synthetic ``db-{pk}``."""
    if row.webhook_id:
        return row.webhook_id
    return f"db-{row.id}"


def row_to_history_dict(row: WebhookEvent) -> dict:
    """Shape expected by ``webhook_row.html`` / ``enrich_webhooks_run_org``."""
    try:
        raw = json.loads(row.raw_json) if row.raw_json else {}
    except json.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {"_non_object_raw": raw}
    return {
        "received_at": row.received_at,
        "event_type": row.event_type,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "webhook_id": webhook_public_id(row),
        "run_id": row.run_id,
        "typed_ref": row.typed_ref,
        "raw": raw,
    }


async def insert_webhook_event(
    session: AsyncSession,
    *,
    webhook_id: str | None,
    run_id: str | None,
    typed_ref: str | None,
    received_at: str,
    event_type: str,
    resource_type: str,
    resource_id: str,
    raw: dict,
) -> None:
    wid = (webhook_id or "").strip() or None
    raw_json = json.dumps(raw)
    rid = (resource_id or "").strip() or ""
    values = dict(
        webhook_id=wid,
        run_id=run_id,
        typed_ref=typed_ref,
        received_at=received_at,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=rid,
        raw_json=raw_json,
    )
    if wid:
        stmt = sqlite_insert(WebhookEvent).values(**values).on_conflict_do_nothing(
            index_elements=["webhook_id"],
        )
    else:
        stmt = insert(WebhookEvent).values(**values)
    await session.execute(stmt)


async def list_webhook_history_dicts_for_run(
    session: AsyncSession,
    run_id: str,
    ctx: RunAccessContext,
) -> list[dict]:
    if await get_run_row_for_access(session, run_id, ctx) is None:
        return []
    result = await session.scalars(
        select(WebhookEvent)
        .where(WebhookEvent.run_id == run_id)
        .order_by(WebhookEvent.received_at.desc()),
    )
    return [row_to_history_dict(r) for r in result.all()]


async def get_webhook_history_dict_for_reader(
    session: AsyncSession,
    public_id: str,
    ctx: RunAccessContext,
) -> dict | None:
    row = await session.scalar(select(WebhookEvent).where(WebhookEvent.webhook_id == public_id))
    if row is None:
        m = _DB_ID_RE.match(public_id)
        if not m:
            return None
        row = await session.get(WebhookEvent, int(m.group(1)))
    if row is None:
        return None

    rid = row.run_id
    if rid is None:
        if not ctx.is_admin:
            return None
    elif await get_run_row_for_access(session, rid, ctx) is None:
        return None

    return row_to_history_dict(row)
