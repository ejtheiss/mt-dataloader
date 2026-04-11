"""SQLite implementation of :class:`dataloader.engine.persist_port.RunStatePersistPort`."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.repositories import run_artifacts, runs as runs_repo
from models.run_execution_entries import ManifestEntry


class SqliteRunStatePersist:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    async def set_config_json(self, run_id: str, config_json: str) -> None:
        async with self._sf() as s:
            await run_artifacts.set_run_config_json(s, run_id, config_json)
            await s.commit()

    async def append_staged_item(
        self,
        run_id: str,
        typed_ref: str,
        resource_type: str,
        staged_at: str,
        payload_json: str,
    ) -> None:
        async with self._sf() as s:
            await run_artifacts.upsert_staged_item(
                s,
                run_id=run_id,
                typed_ref=typed_ref,
                resource_type=resource_type,
                staged_at=staged_at,
                payload_json=payload_json,
            )
            await s.commit()

    async def append_created(self, run_id: str, entry: ManifestEntry) -> None:
        async with self._sf() as s:
            await run_artifacts.insert_created_resource_row(
                s,
                run_id=run_id,
                batch=entry.batch,
                resource_type=entry.resource_type,
                typed_ref=entry.typed_ref,
                created_id=entry.created_id,
                created_at=entry.created_at,
                deletable=entry.deletable,
                child_refs=dict(entry.child_refs),
                cleanup_status=entry.cleanup_status,
            )
            await s.commit()

    async def append_failure(
        self,
        run_id: str,
        typed_ref: str,
        error: str,
        *,
        failed_at: str,
        error_type: str | None,
        http_status: int | None,
        error_cause: str | None,
    ) -> None:
        async with self._sf() as s:
            await run_artifacts.insert_failure_row(
                s,
                run_id=run_id,
                typed_ref=typed_ref,
                error=error,
                failed_at=failed_at,
                error_type=error_type,
                http_status=http_status,
                error_cause=error_cause,
            )
            await s.commit()

    async def finalize(
        self,
        run_id: str,
        status: str,
        completed_at: str | None,
        *,
        resources_created_count: int,
        resources_staged_count: int,
        resources_failed_count: int,
    ) -> None:
        async with self._sf() as s:
            await runs_repo.finalize_run(
                s,
                run_id=run_id,
                status=status,
                completed_at=completed_at,
                resources_created_count=resources_created_count,
                resources_staged_count=resources_staged_count,
                resources_failed_count=resources_failed_count,
            )
            await s.commit()
