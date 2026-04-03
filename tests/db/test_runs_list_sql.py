"""Wave B: SQL-backed run list rows."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from db.database import (
    build_sqlite_file_urls,
    create_async_engine_and_sessionmaker,
    run_alembic_upgrade,
)
from db.repositories import runs as runs_repo
from db.repositories.runs import RunAccessContext
from db.tables import User


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


@pytest.mark.asyncio
async def test_list_run_rows_for_api_reflects_counts(
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
                run_id="r1",
                user_id=1,
                mt_org_id="org_a",
                mt_org_label=None,
                config_hash="h",
                started_at="2026-04-10T00:00:00+00:00",
            )
            await s.commit()
        async with factory() as s:
            await runs_repo.finalize_run(
                s,
                run_id="r1",
                status="completed",
                completed_at="2026-04-10T01:00:00+00:00",
                resources_created_count=3,
                resources_staged_count=1,
                resources_failed_count=2,
            )
            await s.commit()
        async with factory() as s:
            rows = await runs_repo.list_run_rows_for_api(s, RunAccessContext(user_id=1, is_admin=True))
        assert len(rows) == 1
        r0 = rows[0]
        assert r0.run_id == "r1"
        assert r0.status == "completed"
        assert r0.resource_count == 3
        assert r0.staged_count == 1
        assert r0.failed_count == 2
        assert r0.mt_org_id == "org_a"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fetch_manifest_json_roundtrip(
    tmp_path: Path,
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATALOADER_DATA_DIR", str(tmp_path))
    sqlite_path = tmp_path / "dataloader.sqlite"
    sync_url, async_url = build_sqlite_file_urls(sqlite_path)
    run_alembic_upgrade(repo_root, sync_url)
    engine, factory = create_async_engine_and_sessionmaker(async_url)
    payload = '{"run_id":"x","config_hash":"h","started_at":"2026-01-01T00:00:00+00:00","status":"completed","resources_created":[],"resources_failed":[],"resources_staged":[]}'
    try:
        async with factory() as s:
            await runs_repo.ensure_run(
                s,
                run_id="x",
                user_id=1,
                mt_org_id=None,
                mt_org_label=None,
                config_hash="h",
                started_at="2026-01-01T00:00:00+00:00",
            )
            await s.commit()
        async with factory() as s:
            await runs_repo.finalize_run(
                s,
                run_id="x",
                status="completed",
                completed_at="2026-01-01T01:00:00+00:00",
                manifest_json=payload,
            )
            await s.commit()
        async with factory() as s:
            got = await runs_repo.fetch_manifest_json(s, "x", RunAccessContext(user_id=1, is_admin=True))
        assert got == payload
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_run_rows_scoped_to_user(
    tmp_path: Path,
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATALOADER_DATA_DIR", str(tmp_path))
    sqlite_path = tmp_path / "dataloader.sqlite"
    sync_url, async_url = build_sqlite_file_urls(sqlite_path)
    run_alembic_upgrade(repo_root, sync_url)
    engine, factory = create_async_engine_and_sessionmaker(async_url)
    now = datetime.now(timezone.utc).isoformat()
    try:
        async with factory() as s:
            s.add(
                User(
                    id=2,
                    created_at=now,
                    email=None,
                    display_name="u2",
                    role="user",
                )
            )
            s.add(
                User(
                    id=3,
                    created_at=now,
                    email=None,
                    display_name="u3",
                    role="user",
                )
            )
            await s.commit()
        async with factory() as s:
            await runs_repo.ensure_run(
                s,
                run_id="mine",
                user_id=2,
                mt_org_id=None,
                mt_org_label=None,
                config_hash="h",
                started_at="2026-04-10T00:00:00+00:00",
            )
            await runs_repo.ensure_run(
                s,
                run_id="theirs",
                user_id=3,
                mt_org_id=None,
                mt_org_label=None,
                config_hash="h",
                started_at="2026-04-11T00:00:00+00:00",
            )
            await s.commit()
        async with factory() as s:
            rows_user2 = await runs_repo.list_run_rows_for_api(s, RunAccessContext(user_id=2, is_admin=False))
            rows_admin = await runs_repo.list_run_rows_for_api(s, RunAccessContext(user_id=1, is_admin=True))
        assert [r.run_id for r in rows_user2] == ["mine"]
        assert {r.run_id for r in rows_admin} == {"mine", "theirs"}
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_fetch_manifest_json_denied_for_other_user(
    tmp_path: Path,
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATALOADER_DATA_DIR", str(tmp_path))
    sqlite_path = tmp_path / "dataloader.sqlite"
    sync_url, async_url = build_sqlite_file_urls(sqlite_path)
    run_alembic_upgrade(repo_root, sync_url)
    engine, factory = create_async_engine_and_sessionmaker(async_url)
    payload = '{"run_id":"x","config_hash":"h","started_at":"2026-01-01T00:00:00+00:00","status":"completed","resources_created":[],"resources_failed":[],"resources_staged":[]}'
    try:
        async with factory() as s:
            await runs_repo.ensure_run(
                s,
                run_id="x",
                user_id=1,
                mt_org_id=None,
                mt_org_label=None,
                config_hash="h",
                started_at="2026-01-01T00:00:00+00:00",
            )
            await s.commit()
        async with factory() as s:
            await runs_repo.finalize_run(
                s,
                run_id="x",
                status="completed",
                completed_at="2026-01-01T01:00:00+00:00",
                manifest_json=payload,
            )
            await s.commit()
        async with factory() as s:
            got = await runs_repo.fetch_manifest_json(s, "x", RunAccessContext(user_id=2, is_admin=False))
        assert got is None
    finally:
        await engine.dispose()
