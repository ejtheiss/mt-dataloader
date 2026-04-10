"""Run row upserts — metadata mirrored from execution (Wave B)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.expression import ColumnElement

from db.tables import Run
from models.run_list import RunDrawerRow, RunListRow


@dataclass(frozen=True)
class RunAccessContext:
    """Who is reading run data — Plan 0 ``admin`` (all rows) vs ``user`` (``runs.user_id`` only)."""

    user_id: int
    is_admin: bool


@dataclass(frozen=True)
class RunListQueryResult:
    """Result of a scoped runs list query (HTML or JSON)."""

    rows: list[RunListRow]
    has_more: bool = False


_VALID_RUN_LIST_SORT = frozenset({"run_id", "status", "resources", "staged", "failed"})


def _run_list_order_parts(sort: str | None, sort_dir: str) -> tuple[ColumnElement[Any], ...]:
    """Build ``ORDER BY`` fragments matching the HTMX runs table (with stable tie-break)."""

    if not sort or sort not in _VALID_RUN_LIST_SORT:
        # Default list: newest ``started_at`` first (matches legacy ``list_runs`` when no column sort).
        return (Run.started_at.desc(), Run.run_id.desc())

    descending = sort_dir.lower() == "desc"

    def ob(col: Any) -> ColumnElement[Any]:
        return col.desc() if descending else col.asc()
    if sort == "run_id":
        return (ob(Run.run_id), ob(Run.started_at))
    if sort == "status":
        return (ob(Run.status), ob(Run.run_id))
    if sort == "resources":
        return (ob(Run.resources_created_count), ob(Run.run_id))
    if sort == "staged":
        return (ob(Run.resources_staged_count), ob(Run.run_id))
    if sort == "failed":
        return (ob(Run.resources_failed_count), ob(Run.run_id))
    return (ob(Run.started_at), ob(Run.run_id))


async def get_run_row_for_access(
    session: AsyncSession,
    run_id: str,
    ctx: RunAccessContext,
) -> Run | None:
    """Return the ``Run`` row if *run_id* exists and *ctx* may read it; else ``None``."""
    row = await session.scalar(select(Run).where(Run.run_id == run_id))
    if row is None:
        return None
    if ctx.is_admin:
        return row
    if row.user_id is None or row.user_id != ctx.user_id:
        return None
    return row


async def ensure_run(
    session: AsyncSession,
    *,
    run_id: str,
    user_id: int | None,
    mt_org_id: str | None,
    mt_org_label: str | None,
    config_hash: str | None,
    started_at: str,
) -> None:
    stmt = (
        sqlite_insert(Run)
        .values(
            run_id=run_id,
            user_id=user_id,
            mt_org_id=mt_org_id,
            mt_org_label=mt_org_label,
            status="running",
            config_hash=config_hash,
            started_at=started_at,
            completed_at=None,
        )
        .on_conflict_do_nothing(index_elements=["run_id"])
    )
    await session.execute(stmt)


async def update_mt_org(session: AsyncSession, run_id: str, org_id: str) -> None:
    await session.execute(
        update(Run).where(Run.run_id == run_id).values(mt_org_id=org_id),
    )


async def list_run_ids_by_started_desc(session: AsyncSession) -> list[str]:
    """Return ``run_id`` values newest-first by ``started_at`` (ISO string sort)."""
    result = await session.scalars(select(Run.run_id).order_by(Run.started_at.desc()))
    return list(result.all())


async def finalize_run(
    session: AsyncSession,
    *,
    run_id: str,
    status: str,
    completed_at: str | None,
    resources_created_count: int | None = None,
    resources_staged_count: int | None = None,
    resources_failed_count: int | None = None,
) -> None:
    values: dict[str, Any] = {"status": status, "completed_at": completed_at}
    if resources_created_count is not None:
        values["resources_created_count"] = resources_created_count
    if resources_staged_count is not None:
        values["resources_staged_count"] = resources_staged_count
    if resources_failed_count is not None:
        values["resources_failed_count"] = resources_failed_count
    await session.execute(update(Run).where(Run.run_id == run_id).values(**values))


async def fetch_run_drawer_row(
    session: AsyncSession,
    run_id: str,
    ctx: RunAccessContext,
) -> RunDrawerRow | None:
    """DB-backed run summary for the list slide-over drawer (same ``status`` as ``list_run_rows_for_api``)."""
    row = await get_run_row_for_access(session, run_id, ctx)
    if row is None:
        return None
    return RunDrawerRow(
        run_id=row.run_id,
        status=row.status,
        started_at=row.started_at,
        resource_count=row.resources_created_count,
        staged_count=row.resources_staged_count,
        failed_count=row.resources_failed_count,
        mt_org_id=row.mt_org_id,
        completed_at=row.completed_at,
        config_hash=row.config_hash,
    )


async def query_run_rows_for_api(
    session: AsyncSession,
    ctx: RunAccessContext,
    *,
    status: str | None = None,
    mt_org_id: str | None = None,
    sort: str | None = None,
    sort_dir: str = "asc",
    limit: int | None = None,
    offset: int = 0,
    fetch_extra_for_has_more: bool = False,
) -> RunListQueryResult:
    """Run rows visible to *ctx* with optional filters, sort, and SQL ``LIMIT``/``OFFSET``.

    Filters and sort are applied in the database (not after fetch). When *limit* is set and
    *fetch_extra_for_has_more* is true, fetches *limit* + 1 rows and sets ``has_more`` accordingly.
    """
    stmt = select(Run)
    if not ctx.is_admin:
        stmt = stmt.where(Run.user_id == ctx.user_id)
    if status:
        stmt = stmt.where(Run.status == status)
    if mt_org_id and mt_org_id.strip():
        stmt = stmt.where(Run.mt_org_id == mt_org_id.strip())

    for part in _run_list_order_parts(sort, sort_dir):
        stmt = stmt.order_by(part)

    has_more = False
    if limit is not None:
        cap = limit + 1 if fetch_extra_for_has_more else limit
        stmt = stmt.limit(cap).offset(max(0, offset))

    result = await session.scalars(stmt)
    orm_rows = list(result.all())

    if limit is not None and fetch_extra_for_has_more and len(orm_rows) > limit:
        has_more = True
        orm_rows = orm_rows[:limit]

    rows = [
        RunListRow(
            run_id=r.run_id,
            status=r.status,
            started_at=r.started_at,
            resource_count=r.resources_created_count,
            staged_count=r.resources_staged_count,
            failed_count=r.resources_failed_count,
            mt_org_id=r.mt_org_id,
        )
        for r in orm_rows
    ]
    return RunListQueryResult(rows=rows, has_more=has_more)


async def list_run_rows_for_api(
    session: AsyncSession,
    ctx: RunAccessContext,
) -> list[RunListRow]:
    """Run rows visible to *ctx*, default sort (newest ``started_at`` first).

    ``GET /api/runs`` uses this when the DB is up (no disk glob for ``user``).
    """
    res = await query_run_rows_for_api(session, ctx, limit=None, offset=0)
    return res.rows


async def list_run_id_set(session: AsyncSession) -> set[str]:
    """All ``run_id`` primary keys (for idempotent backfill)."""
    result = await session.scalars(select(Run.run_id))
    return set(result.all())


async def backfill_upsert_run(
    session: AsyncSession,
    *,
    run_id: str,
    user_id: int | None,
    mt_org_id: str | None,
    mt_org_label: str | None,
    status: str,
    config_hash: str | None,
    started_at: str,
    completed_at: str | None,
    resources_created_count: int = 0,
    resources_staged_count: int = 0,
    resources_failed_count: int = 0,
) -> None:
    """Insert or replace run metadata from disk manifest (historical import)."""
    set_: dict[str, Any] = {
        "user_id": user_id,
        "mt_org_id": mt_org_id,
        "mt_org_label": mt_org_label,
        "status": status,
        "config_hash": config_hash,
        "started_at": started_at,
        "completed_at": completed_at,
        "resources_created_count": resources_created_count,
        "resources_staged_count": resources_staged_count,
        "resources_failed_count": resources_failed_count,
    }
    stmt = (
        sqlite_insert(Run)
        .values(
            run_id=run_id,
            user_id=user_id,
            mt_org_id=mt_org_id,
            mt_org_label=mt_org_label,
            status=status,
            config_hash=config_hash,
            started_at=started_at,
            completed_at=completed_at,
            resources_created_count=resources_created_count,
            resources_staged_count=resources_staged_count,
            resources_failed_count=resources_failed_count,
        )
        .on_conflict_do_update(
            index_elements=["run_id"],
            set_=set_,
        )
    )
    await session.execute(stmt)


async def fetch_run_mt_org_rows(session: AsyncSession) -> list[tuple[str, str]]:
    """``(run_id, mt_org_id)`` for runs with a non-null org (webhook UI enrichment)."""
    result = await session.execute(
        select(Run.run_id, Run.mt_org_id).where(Run.mt_org_id.isnot(None))
    )
    return [(rid, oid) for rid, oid in result.all() if oid]


async def map_mt_org_ids_for_run_ids(
    session: AsyncSession,
    run_ids: list[str],
) -> dict[str, str]:
    """``run_id`` → ``mt_org_id`` for the given ids (non-null org only)."""
    if not run_ids:
        return {}
    result = await session.execute(
        select(Run.run_id, Run.mt_org_id).where(
            Run.run_id.in_(run_ids),
            Run.mt_org_id.is_not(None),
        )
    )
    return {rid: oid for rid, oid in result.all() if oid}
