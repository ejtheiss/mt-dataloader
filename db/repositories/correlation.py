"""Resource ID → run correlation (webhook matching)."""

from __future__ import annotations

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.tables import ResourceCorrelation


async def upsert_correlation(
    session: AsyncSession,
    *,
    created_id: str,
    run_id: str,
    typed_ref: str,
) -> None:
    if not created_id or created_id == "SKIPPED":
        return
    stmt = (
        sqlite_insert(ResourceCorrelation)
        .values(
            created_id=created_id,
            run_id=run_id,
            typed_ref=typed_ref,
        )
        .on_conflict_do_update(
            index_elements=["created_id"],
            set_={
                "run_id": run_id,
                "typed_ref": typed_ref,
            },
        )
    )
    await session.execute(stmt)
