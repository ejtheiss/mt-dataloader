"""Run row upserts — metadata mirror; manifest JSON stays on disk until a later wave."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.tables import Run
from models.run_list import RunListRow


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


async def list_run_rows_for_api(session: AsyncSession) -> list[RunListRow]:
    """Wave B: all run rows for list UI, ordered newest-first by ``started_at`` (SQL only).

    Callers merge with disk-only manifests and apply ``status`` / ``mt_org_id`` filters
    in memory so filters apply uniformly.
    """
    stmt = select(Run).order_by(Run.started_at.desc())
    result = await session.scalars(stmt)
    return [
        RunListRow(
            run_id=r.run_id,
            status=r.status,
            started_at=r.started_at,
            resource_count=r.resources_created_count,
            staged_count=r.resources_staged_count,
            failed_count=r.resources_failed_count,
            mt_org_id=r.mt_org_id,
        )
        for r in result.all()
    ]


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
            set_={
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
            },
        )
    )
    await session.execute(stmt)


async def fetch_run_mt_org_rows(session: AsyncSession) -> list[tuple[str, str]]:
    """``(run_id, mt_org_id)`` for runs with a non-null org (webhook UI enrichment)."""
    result = await session.execute(
        select(Run.run_id, Run.mt_org_id).where(Run.mt_org_id.isnot(None))
    )
    return [(rid, oid) for rid, oid in result.all() if oid]
