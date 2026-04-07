"""Wave D: ``loader_drafts`` repository."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from db.database import (
    build_sqlite_file_urls,
    create_async_engine_and_sessionmaker,
    run_alembic_upgrade,
)
from db.repositories import loader_drafts as drafts_repo
from db.repositories.runs import RunAccessContext
from db.tables import LoaderDraftRow, User
from models.loader_draft import LoaderDraft


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _draft(org: str = "org_1") -> LoaderDraft:
    return LoaderDraft(
        org_id=org,
        config_json_text="{}",
        batches=[["a.1"]],
        skip_refs=["r1"],
    )


@pytest.mark.asyncio
async def test_upsert_get_roundtrip(
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
    ctx = RunAccessContext(user_id=1, is_admin=False)
    try:
        async with factory() as s:
            await drafts_repo.upsert_loader_draft(
                s,
                user_id=1,
                ctx=ctx,
                draft=_draft(),
                updated_at=now,
                last_run_id="run-a",
                last_run_at=now,
            )
            await s.commit()
        async with factory() as s:
            parsed = await drafts_repo.get_loader_draft(s, 1, ctx)
        assert parsed is not None
        assert parsed.org_id == "org_1"
        assert parsed.batches == [["a.1"]]
        assert parsed.skip_refs == ["r1"]
        async with factory() as s:
            row = await drafts_repo.get_loader_draft_row(s, 1, ctx)
        assert row is not None
        assert row.last_run_id == "run-a"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_user_cannot_read_other_draft(
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
            await drafts_repo.upsert_loader_draft(
                s,
                user_id=1,
                ctx=RunAccessContext(user_id=1, is_admin=False),
                draft=_draft("owner"),
                updated_at=now,
            )
            await s.commit()
        async with factory() as s:
            row = await drafts_repo.get_loader_draft_row(
                s, 1, RunAccessContext(user_id=2, is_admin=False)
            )
            parsed = await drafts_repo.get_loader_draft(
                s, 1, RunAccessContext(user_id=2, is_admin=False)
            )
        assert row is None
        assert parsed is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_admin_can_read_other_user_draft(
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
            await drafts_repo.upsert_loader_draft(
                s,
                user_id=1,
                ctx=RunAccessContext(user_id=1, is_admin=False),
                draft=_draft("admin_visible"),
                updated_at=now,
            )
            await s.commit()
        async with factory() as s:
            parsed = await drafts_repo.get_loader_draft(
                s, 1, RunAccessContext(user_id=99, is_admin=True)
            )
        assert parsed is not None
        assert parsed.org_id == "admin_visible"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_upsert_forbidden_for_other_user(
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
            with pytest.raises(PermissionError):
                await drafts_repo.upsert_loader_draft(
                    s,
                    user_id=1,
                    ctx=RunAccessContext(user_id=2, is_admin=False),
                    draft=_draft(),
                    updated_at=now,
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_invalid_draft_json_raises(
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
            await s.execute(
                text(
                    "INSERT INTO loader_drafts (user_id, draft_json, updated_at) VALUES (1, :j, :t)"
                ).bindparams(j="not json", t=now)
            )
            await s.commit()
        async with factory() as s:
            with pytest.raises(ValidationError):
                await drafts_repo.get_loader_draft(
                    s, 1, RunAccessContext(user_id=1, is_admin=False)
                )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_and_prune(
    tmp_path: Path,
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATALOADER_DATA_DIR", str(tmp_path))
    sqlite_path = tmp_path / "dataloader.sqlite"
    sync_url, async_url = build_sqlite_file_urls(sqlite_path)
    run_alembic_upgrade(repo_root, sync_url)
    engine, factory = create_async_engine_and_sessionmaker(async_url)
    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=40)).isoformat()
    fresh = now.isoformat()
    try:
        async with factory() as s:
            s.add(
                User(
                    id=2,
                    created_at=fresh,
                    email=None,
                    display_name="u2",
                    role="user",
                )
            )
            await drafts_repo.upsert_loader_draft(
                s,
                user_id=1,
                ctx=RunAccessContext(user_id=1, is_admin=False),
                draft=_draft("u1"),
                updated_at=old,
            )
            await drafts_repo.upsert_loader_draft(
                s,
                user_id=2,
                ctx=RunAccessContext(user_id=2, is_admin=False),
                draft=_draft("u2"),
                updated_at=fresh,
            )
            await s.commit()
        async with factory() as s:
            n = await drafts_repo.prune_loader_drafts_older_than(s, fresh)
            await s.commit()
        assert n == 1
        async with factory() as s:
            r1 = await s.get(LoaderDraftRow, 1)
            r2 = await s.get(LoaderDraftRow, 2)
        assert r1 is None
        assert r2 is not None

        async with factory() as s:
            ok = await drafts_repo.delete_loader_draft(
                s, 2, RunAccessContext(user_id=2, is_admin=False)
            )
            await s.commit()
        assert ok is True
        async with factory() as s:
            r2b = await s.get(LoaderDraftRow, 2)
        assert r2b is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_update_last_run_preserves_draft_json(
    tmp_path: Path,
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATALOADER_DATA_DIR", str(tmp_path))
    sqlite_path = tmp_path / "dataloader.sqlite"
    sync_url, async_url = build_sqlite_file_urls(sqlite_path)
    run_alembic_upgrade(repo_root, sync_url)
    engine, factory = create_async_engine_and_sessionmaker(async_url)
    t0 = datetime.now(timezone.utc).isoformat()
    t1 = (datetime.now(timezone.utc)).isoformat()
    ctx = RunAccessContext(user_id=1, is_admin=False)
    try:
        async with factory() as s:
            await drafts_repo.upsert_loader_draft(
                s,
                user_id=1,
                ctx=ctx,
                draft=_draft("x"),
                updated_at=t0,
            )
            await s.commit()
        async with factory() as s:
            ok = await drafts_repo.update_last_run(
                s,
                user_id=1,
                ctx=ctx,
                last_run_id="r99",
                last_run_at=t1,
                updated_at=t1,
            )
            await s.commit()
        assert ok is True
        async with factory() as s:
            parsed = await drafts_repo.get_loader_draft(s, 1, ctx)
            row = await drafts_repo.get_loader_draft_row(s, 1, ctx)
        assert parsed is not None and parsed.org_id == "x"
        assert row is not None
        assert row.last_run_id == "r99"
        assert row.last_run_at == t1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_update_last_run_without_row_returns_false(
    tmp_path: Path,
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATALOADER_DATA_DIR", str(tmp_path))
    sqlite_path = tmp_path / "dataloader.sqlite"
    sync_url, async_url = build_sqlite_file_urls(sqlite_path)
    run_alembic_upgrade(repo_root, sync_url)
    engine, factory = create_async_engine_and_sessionmaker(async_url)
    t = datetime.now(timezone.utc).isoformat()
    ctx = RunAccessContext(user_id=1, is_admin=False)
    try:
        async with factory() as s:
            ok = await drafts_repo.update_last_run(
                s,
                user_id=1,
                ctx=ctx,
                last_run_id="r1",
                last_run_at=t,
                updated_at=t,
            )
            await s.commit()
        assert ok is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_prune_rejects_non_iso_cutoff(
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
            with pytest.raises(ValueError, match="cutoff_iso"):
                await drafts_repo.prune_loader_drafts_older_than(s, "not-a-timestamp")
    finally:
        await engine.dispose()
