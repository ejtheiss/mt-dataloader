"""Durable loader drafts — one row per ``users.id`` (Wave D).

**Access:** ``RunAccessContext`` matches runs — owners read/write their row;
``is_admin`` may read/write **any** ``user_id``. Product/UI must pass the
intended ``user_id`` explicitly; there is no implicit “active user” here.
Cross-user writes are easy to misuse — reserve for operator tools only.

**Prune:** ``prune_loader_drafts_older_than`` compares ``updated_at`` to
``cutoff_iso`` as strings (SQLite). Callers must use a single convention —
``datetime.now(timezone.utc).isoformat()`` — or lexicographic order can mis-order
rows. The cutoff string is validated as parseable ISO 8601 before DELETE.

**Execute (03b):** Do not call ``delete_loader_draft`` as part of normal run
start; durable rows survive execute per Plan 0 invariant.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.repositories.runs import RunAccessContext
from db.tables import LoaderDraftRow
from models.loader_draft import LoaderDraft


def _validate_cutoff_iso8601(cutoff_iso: str) -> None:
    """Reject garbage cutoffs; normalize trailing Z for ``fromisoformat``."""
    try:
        datetime.fromisoformat(cutoff_iso.replace("Z", "+00:00"))
    except ValueError as e:
        raise ValueError(
            "cutoff_iso must be ISO 8601, e.g. datetime.now(timezone.utc).isoformat(). "
            "Prune uses string compare on updated_at — keep one timestamp format in the DB."
        ) from e


def _may_access_row(user_id: int, ctx: RunAccessContext) -> bool:
    return ctx.is_admin or ctx.user_id == user_id


async def get_loader_draft_row(
    session: AsyncSession,
    user_id: int,
    ctx: RunAccessContext,
) -> LoaderDraftRow | None:
    """Return the row for *user_id* if *ctx* may read it."""
    if not _may_access_row(user_id, ctx):
        return None
    return await session.get(LoaderDraftRow, user_id)


async def get_loader_draft(
    session: AsyncSession,
    user_id: int,
    ctx: RunAccessContext,
) -> LoaderDraft | None:
    """Parse ``draft_json`` into ``LoaderDraft``, or ``None`` if missing or forbidden."""
    row = await get_loader_draft_row(session, user_id, ctx)
    if row is None:
        return None
    return LoaderDraft.model_validate_json(row.draft_json)


async def upsert_loader_draft(
    session: AsyncSession,
    *,
    user_id: int,
    ctx: RunAccessContext,
    draft: LoaderDraft,
    updated_at: str,
    last_run_id: str | None = None,
    last_run_at: str | None = None,
) -> None:
    """Insert or replace the draft for *user_id*. Raises ``PermissionError`` if *ctx* cannot write."""
    if not _may_access_row(user_id, ctx):
        raise PermissionError("not allowed to write loader draft for this user")

    body_json = draft.model_dump_json()
    ins = sqlite_insert(LoaderDraftRow).values(
        user_id=user_id,
        draft_json=body_json,
        updated_at=updated_at,
        last_run_id=last_run_id,
        last_run_at=last_run_at,
    )
    stmt = ins.on_conflict_do_update(
        index_elements=["user_id"],
        set_={
            "draft_json": ins.excluded.draft_json,
            "updated_at": ins.excluded.updated_at,
            "last_run_id": ins.excluded.last_run_id,
            "last_run_at": ins.excluded.last_run_at,
        },
    )
    await session.execute(stmt)


async def update_last_run(
    session: AsyncSession,
    *,
    user_id: int,
    ctx: RunAccessContext,
    last_run_id: str | None,
    last_run_at: str | None,
    updated_at: str,
) -> bool:
    """Patch run metadata without replacing ``draft_json``.

    Returns ``False`` if no row exists (callers must not assume metadata was
    recorded — e.g. run ``upsert_loader_draft`` first when a draft is required).
    """
    if not _may_access_row(user_id, ctx):
        raise PermissionError("not allowed to update loader draft for this user")

    row = await session.get(LoaderDraftRow, user_id)
    if row is None:
        return False
    row.last_run_id = last_run_id
    row.last_run_at = last_run_at
    row.updated_at = updated_at
    return True


async def delete_loader_draft(
    session: AsyncSession,
    user_id: int,
    ctx: RunAccessContext,
) -> bool:
    """Remove the draft row. Returns whether a row was deleted."""
    if not _may_access_row(user_id, ctx):
        return False
    row = await session.get(LoaderDraftRow, user_id)
    if row is None:
        return False
    await session.delete(row)
    return True


async def prune_loader_drafts_older_than(session: AsyncSession, cutoff_iso: str) -> int:
    """Delete drafts with ``updated_at`` strictly before *cutoff_iso* (string ``<`` in SQL)."""
    _validate_cutoff_iso8601(cutoff_iso)
    result = await session.execute(
        delete(LoaderDraftRow).where(LoaderDraftRow.updated_at < cutoff_iso)
    )
    return int(result.rowcount or 0)
