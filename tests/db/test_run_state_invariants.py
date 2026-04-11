"""Invariant checks: normalized artifacts vs denormalized ``runs`` counters + correlation expansion."""

from __future__ import annotations

from pathlib import Path

import pytest

from db.database import (
    build_sqlite_file_urls,
    create_async_engine_and_sessionmaker,
    run_alembic_upgrade,
)
from db.repositories import run_artifacts, runs as runs_repo
from db.repositories.runs import RunAccessContext
from db.tables import Run


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


@pytest.mark.asyncio
async def test_sync_artifact_counts_matches_table_rows(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    sqlite_path = tmp_path / "dataloader.sqlite"
    sync_url, async_url = build_sqlite_file_urls(sqlite_path)
    run_alembic_upgrade(repo_root, sync_url)
    engine, factory = create_async_engine_and_sessionmaker(async_url)
    run_id = "inv-counts-1"
    try:
        async with factory() as s:
            await runs_repo.ensure_run(
                s,
                run_id=run_id,
                user_id=1,
                mt_org_id=None,
                mt_org_label=None,
                config_hash="h",
                started_at="2026-01-01T00:00:00+00:00",
            )
            await run_artifacts.insert_created_resource_row(
                s,
                run_id=run_id,
                batch=0,
                resource_type="ledger",
                typed_ref="ledgers.main",
                created_id="la_1",
                created_at="2026-01-01T00:00:01+00:00",
                deletable=False,
                child_refs={},
            )
            await run_artifacts.upsert_staged_item(
                s,
                run_id=run_id,
                typed_ref="payment_orders.x",
                resource_type="payment_order",
                staged_at="2026-01-01T00:00:02+00:00",
                payload_json="{}",
            )
            await run_artifacts.insert_failure_row(
                s,
                run_id=run_id,
                typed_ref="bad.ref",
                error="boom",
                failed_at="2026-01-01T00:00:03+00:00",
                error_type="ValueError",
                http_status=None,
                error_cause=None,
            )
            await runs_repo.sync_artifact_counts_from_tables(s, run_id)
            await s.commit()

        async with factory() as s:
            row = await s.get(Run, run_id)
        assert row is not None
        assert row.resources_created_count == 1
        assert row.resources_staged_count == 1
        assert row.resources_failed_count == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_correlation_index_includes_child_ref_targets(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    sqlite_path = tmp_path / "dataloader.sqlite"
    sync_url, async_url = build_sqlite_file_urls(sqlite_path)
    run_alembic_upgrade(repo_root, sync_url)
    engine, factory = create_async_engine_and_sessionmaker(async_url)
    run_id = "inv-corr-1"
    try:
        async with factory() as s:
            await runs_repo.ensure_run(
                s,
                run_id=run_id,
                user_id=1,
                mt_org_id=None,
                mt_org_label=None,
                config_hash="h",
                started_at="2026-01-01T00:00:00+00:00",
            )
            await run_artifacts.insert_created_resource_row(
                s,
                run_id=run_id,
                batch=0,
                resource_type="payment_order",
                typed_ref="payment_orders.po1",
                created_id="po_parent",
                created_at="2026-01-01T00:00:01+00:00",
                deletable=False,
                child_refs={"ledger_transaction": "lt_child"},
            )
            await s.commit()

        async with factory() as s:
            rows = await run_artifacts.fetch_correlation_index_rows(s)
        ids = {r[0]: (r[1], r[2]) for r in rows}
        assert ids["po_parent"] == (run_id, "payment_orders.po1")
        assert ids["lt_child"] == (run_id, "payment_orders.po1.ledger_transaction")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_access_scoping_non_owner_sees_no_artifacts(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    sqlite_path = tmp_path / "dataloader.sqlite"
    sync_url, async_url = build_sqlite_file_urls(sqlite_path)
    run_alembic_upgrade(repo_root, sync_url)
    engine, factory = create_async_engine_and_sessionmaker(async_url)
    run_id = "inv-access-1"
    try:
        async with factory() as s:
            await runs_repo.ensure_run(
                s,
                run_id=run_id,
                user_id=1,
                mt_org_id=None,
                mt_org_label=None,
                config_hash="h",
                started_at="2026-01-01T00:00:00+00:00",
            )
            await run_artifacts.insert_created_resource_row(
                s,
                run_id=run_id,
                batch=0,
                resource_type="ledger",
                typed_ref="ledgers.main",
                created_id="la_1",
                created_at="2026-01-01T00:00:01+00:00",
                deletable=False,
                child_refs={},
            )
            await s.commit()

        other = RunAccessContext(user_id=2, is_admin=False)
        async with factory() as s:
            cleanup_rows = await run_artifacts.fetch_cleanup_created_rows(s, run_id, other)
            detail = await run_artifacts.fetch_run_detail_view(s, run_id, other)
        assert cleanup_rows == []
        assert detail is None
    finally:
        await engine.dispose()
