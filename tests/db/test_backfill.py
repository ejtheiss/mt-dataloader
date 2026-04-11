"""Manifest → DB backfill and webhook index hydration."""

from __future__ import annotations

from pathlib import Path

import pytest

from dataloader.db_backfill import (
    backfill_missing_runs_from_disk,
    bootstrap_webhook_correlation,
    load_runtime_correlation_from_db,
)
from dataloader.webhooks.correlation_state import (
    correlate_inbound_payload,
    correlation_index_size,
    replace_runtime_correlation_state,
)
from db.database import (
    build_sqlite_file_urls,
    create_async_engine_and_sessionmaker,
    run_alembic_upgrade,
)
from db.repositories import runs as runs_repo
from jsonutil import dumps_pretty


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


@pytest.fixture(autouse=True)
def _reset_webhook_correlation_index() -> None:
    """Process-global maps must not leak between tests."""
    replace_runtime_correlation_state([], [])
    yield
    replace_runtime_correlation_state([], [])


def _write_manifest(path: Path, run_id: str, *, created_id: str = "res_abc") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "run_id": run_id,
        "config_hash": "sha256:00",
        "started_at": "2026-04-02T12:00:00+00:00",
        "completed_at": "2026-04-02T12:01:00+00:00",
        "status": "completed",
        "mt_org_id": "org_test",
        "mt_org_label": "Test Org",
        "resources_created": [
            {
                "batch": 0,
                "resource_type": "internal_account",
                "typed_ref": "internal_accounts.primary",
                "created_id": created_id,
                "created_at": "2026-04-02T12:00:01+00:00",
                "deletable": True,
                "child_refs": {},
            }
        ],
        "resources_failed": [],
        "resources_staged": [],
    }
    path.write_text(dumps_pretty(doc), encoding="utf-8")


@pytest.mark.asyncio
async def test_backfill_inserts_run_and_correlation_when_missing(
    tmp_path: Path,
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATALOADER_DATA_DIR", str(tmp_path))
    runs_dir = tmp_path / "runs"
    run_id = "20260402T120000_deadbeef"
    _write_manifest(runs_dir / f"{run_id}.json", run_id)

    sqlite_path = tmp_path / "dataloader.sqlite"
    sync_url, async_url = build_sqlite_file_urls(sqlite_path)
    run_alembic_upgrade(repo_root, sync_url)
    engine, factory = create_async_engine_and_sessionmaker(async_url)
    try:
        async with factory() as s:
            stats = await backfill_missing_runs_from_disk(s, runs_dir, default_user_id=1)
            await s.commit()
        assert stats["runs_backfilled"] == 1
        assert stats["artifact_rows"] >= 1

        async with factory() as s:
            ids = await runs_repo.list_run_ids_by_started_desc(s)
        assert ids == [run_id]

        async with factory() as s:
            await load_runtime_correlation_from_db(s)
        assert correlation_index_size() == 1
        rid, tref = correlate_inbound_payload({"id": "res_abc"})
        assert rid == run_id
        assert tref == "internal_accounts.primary"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_skips_when_run_row_exists(
    tmp_path: Path,
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATALOADER_DATA_DIR", str(tmp_path))
    runs_dir = tmp_path / "runs"
    run_id = "20260402T130000_cafebabe"
    _write_manifest(runs_dir / f"{run_id}.json", run_id)

    sqlite_path = tmp_path / "dataloader.sqlite"
    sync_url, async_url = build_sqlite_file_urls(sqlite_path)
    run_alembic_upgrade(repo_root, sync_url)
    engine, factory = create_async_engine_and_sessionmaker(async_url)
    try:
        async with factory() as s:
            await runs_repo.ensure_run(
                s,
                run_id=run_id,
                user_id=1,
                mt_org_id=None,
                mt_org_label=None,
                config_hash="x",
                started_at="2026-04-02T13:00:00+00:00",
            )
            await s.commit()

        async with factory() as s:
            stats = await backfill_missing_runs_from_disk(s, runs_dir, default_user_id=1)
            await s.commit()
        assert stats["runs_backfilled"] == 0
        assert stats["artifact_rows"] == 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_bootstrap_webhook_correlation_end_to_end(
    tmp_path: Path,
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATALOADER_DATA_DIR", str(tmp_path))
    runs_dir = tmp_path / "runs"
    run_id = "20260402T140000_aabbccdd"
    _write_manifest(runs_dir / f"{run_id}.json", run_id, created_id="uuid-end2end")

    sqlite_path = tmp_path / "dataloader.sqlite"
    sync_url, async_url = build_sqlite_file_urls(sqlite_path)
    run_alembic_upgrade(repo_root, sync_url)
    engine, factory = create_async_engine_and_sessionmaker(async_url)
    try:
        out = await bootstrap_webhook_correlation(factory, str(runs_dir), default_user_id=1)
        assert out["runs_backfilled"] == 1
        assert out["index_ids"] == 1
        rid, _t = correlate_inbound_payload({"id": "uuid-end2end"})
        assert rid == run_id
    finally:
        await engine.dispose()
