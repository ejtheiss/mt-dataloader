"""Wave C: ``webhook_events`` repository."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import select

from db.database import (
    build_sqlite_file_urls,
    create_async_engine_and_sessionmaker,
    run_alembic_upgrade,
)
from db.repositories import runs as runs_repo
from db.repositories import webhooks as webhooks_repo
from db.repositories.runs import RunAccessContext
from db.tables import User, WebhookEvent


@pytest.fixture
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


@pytest.mark.asyncio
async def test_insert_list_webhook_for_run(
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
            await runs_repo.ensure_run(
                s,
                run_id="r1",
                user_id=1,
                mt_org_id=None,
                mt_org_label=None,
                config_hash="h",
                started_at=now,
            )
            await s.commit()
        async with factory() as s:
            await webhooks_repo.insert_webhook_event(
                s,
                webhook_id="wh-unique-1",
                run_id="r1",
                typed_ref="ledger_transaction.lt1",
                received_at=now,
                event_type="ledger_transaction.created",
                resource_type="ledger_transaction",
                resource_id="lt_123",
                raw={"event": "created", "data": {"id": "lt_123"}},
            )
            await s.commit()
        async with factory() as s:
            rows = await webhooks_repo.list_webhook_history_dicts_for_run(
                s, "r1", RunAccessContext(user_id=1, is_admin=False)
            )
        assert len(rows) == 1
        assert rows[0]["webhook_id"] == "wh-unique-1"
        assert rows[0]["run_id"] == "r1"
        assert rows[0]["raw"]["data"]["id"] == "lt_123"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_user_cannot_list_other_run_webhooks(
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
            await runs_repo.ensure_run(
                s,
                run_id="r1",
                user_id=1,
                mt_org_id=None,
                mt_org_label=None,
                config_hash="h",
                started_at=now,
            )
            await s.commit()
        async with factory() as s:
            await webhooks_repo.insert_webhook_event(
                s,
                webhook_id="wh-x",
                run_id="r1",
                typed_ref=None,
                received_at=now,
                event_type="e",
                resource_type="t",
                resource_id="",
                raw={},
            )
            await s.commit()
        async with factory() as s:
            rows = await webhooks_repo.list_webhook_history_dicts_for_run(
                s, "r1", RunAccessContext(user_id=2, is_admin=False)
            )
        assert rows == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_get_by_db_public_id_and_unmatched_admin_only(
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
            await webhooks_repo.insert_webhook_event(
                s,
                webhook_id=None,
                run_id=None,
                typed_ref=None,
                received_at=now,
                event_type="e",
                resource_type="t",
                resource_id="",
                raw={"k": 1},
            )
            await s.commit()
        async with factory() as s:
            wid = await s.scalar(select(WebhookEvent.id).order_by(WebhookEvent.id.desc()).limit(1))
        assert wid is not None
        public = f"db-{wid}"
        async with factory() as s:
            u = await webhooks_repo.get_webhook_history_dict_for_reader(
                s, public, RunAccessContext(user_id=1, is_admin=False)
            )
            a = await webhooks_repo.get_webhook_history_dict_for_reader(
                s, public, RunAccessContext(user_id=1, is_admin=True)
            )
        assert u is None
        assert a is not None
        assert a["webhook_id"] == public
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_list_run_ids_with_webhooks_ordered_and_listener_feed(
    tmp_path: Path,
    repo_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATALOADER_DATA_DIR", str(tmp_path))
    sqlite_path = tmp_path / "dataloader.sqlite"
    sync_url, async_url = build_sqlite_file_urls(sqlite_path)
    run_alembic_upgrade(repo_root, sync_url)
    engine, factory = create_async_engine_and_sessionmaker(async_url)
    t_old = "2026-01-01T00:00:00+00:00"
    t_new = "2026-02-01T00:00:00+00:00"
    try:
        async with factory() as s:
            s.add(
                User(
                    id=2,
                    created_at=t_old,
                    email=None,
                    display_name="u2",
                    role="user",
                )
            )
            await runs_repo.ensure_run(
                s,
                run_id="r_old",
                user_id=1,
                mt_org_id=None,
                mt_org_label=None,
                config_hash="h",
                started_at=t_old,
            )
            await runs_repo.ensure_run(
                s,
                run_id="r_new",
                user_id=1,
                mt_org_id=None,
                mt_org_label=None,
                config_hash="h",
                started_at=t_new,
            )
            await runs_repo.ensure_run(
                s,
                run_id="r_other",
                user_id=2,
                mt_org_id=None,
                mt_org_label=None,
                config_hash="h",
                started_at=t_new,
            )
            await s.commit()
        async with factory() as s:
            await webhooks_repo.insert_webhook_event(
                s,
                webhook_id="w-old",
                run_id="r_old",
                typed_ref=None,
                received_at=t_old,
                event_type="e",
                resource_type="t",
                resource_id="",
                raw={},
            )
            await webhooks_repo.insert_webhook_event(
                s,
                webhook_id="w-new",
                run_id="r_new",
                typed_ref=None,
                received_at=t_new,
                event_type="e",
                resource_type="t",
                resource_id="",
                raw={},
            )
            await webhooks_repo.insert_webhook_event(
                s,
                webhook_id="w-other",
                run_id="r_other",
                typed_ref=None,
                received_at=t_new,
                event_type="e",
                resource_type="t",
                resource_id="",
                raw={},
            )
            await webhooks_repo.insert_webhook_event(
                s,
                webhook_id=None,
                run_id=None,
                typed_ref=None,
                received_at=t_new,
                event_type="unmatched",
                resource_type="t",
                resource_id="",
                raw={},
            )
            await s.commit()

        ctx1 = RunAccessContext(user_id=1, is_admin=False)
        ctx_admin = RunAccessContext(user_id=1, is_admin=True)
        async with factory() as s:
            ids_user = await webhooks_repo.list_run_ids_with_webhooks_ordered(s, ctx1)
            ids_admin = await webhooks_repo.list_run_ids_with_webhooks_ordered(s, ctx_admin)
            feed_user = await webhooks_repo.list_recent_webhook_history_for_listener(
                s, ctx1, limit=10
            )
            feed_admin = await webhooks_repo.list_recent_webhook_history_for_listener(
                s, ctx_admin, limit=10
            )

        assert ids_user == ["r_new", "r_old"]
        assert set(ids_admin) == {"r_old", "r_new", "r_other"}
        assert ids_admin[-1] == "r_old"
        assert {row["run_id"] for row in feed_user} == {"r_old", "r_new"}
        admin_run_ids = {row["run_id"] for row in feed_admin}
        assert "r_other" in admin_run_ids
        assert None in admin_run_ids
        assert len(feed_admin) == 4
        assert all(row["received_at"] == t_new for row in feed_admin[:3])
    finally:
        await engine.dispose()
