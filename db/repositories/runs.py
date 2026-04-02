"""Run row upserts — metadata mirror; manifest JSON stays on disk until a later wave."""

from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.tables import Run


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
