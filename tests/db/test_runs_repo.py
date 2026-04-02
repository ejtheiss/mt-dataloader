"""Repository helpers for ``runs`` table."""

from __future__ import annotations

from pathlib import Path

import pytest

from db.database import (
    build_sqlite_file_urls,
    create_async_engine_and_sessionmaker,
    run_alembic_upgrade,
)
from db.repositories import runs as runs_repo


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


@pytest.mark.asyncio
async def test_list_run_ids_by_started_desc_orders_newest_first(
    tmp_path: Path,
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATALOADER_DATA_DIR", str(tmp_path))
    sqlite_path = tmp_path / "dataloader.sqlite"
    sync_url, async_url = build_sqlite_file_urls(sqlite_path)
    run_alembic_upgrade(repo_root, sync_url)
    engine, factory = create_async_engine_and_sessionmaker(async_url)
    try:
        async with factory() as s:
            await runs_repo.ensure_run(
                s,
                run_id="older",
                user_id=1,
                mt_org_id=None,
                mt_org_label=None,
                config_hash="h1",
                started_at="2026-01-02T00:00:00+00:00",
            )
            await runs_repo.ensure_run(
                s,
                run_id="newer",
                user_id=1,
                mt_org_id=None,
                mt_org_label=None,
                config_hash="h2",
                started_at="2026-01-03T00:00:00+00:00",
            )
            await s.commit()
        async with factory() as s:
            ids = await runs_repo.list_run_ids_by_started_desc(s)
        assert ids == ["newer", "older"]

        async with factory() as s:
            await runs_repo.finalize_run(
                s,
                run_id="newer",
                status="completed",
                completed_at="2026-01-03T01:00:00+00:00",
            )
            await s.commit()
    finally:
        await engine.dispose()
