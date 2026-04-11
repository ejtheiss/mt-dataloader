"""Plan 0: Alembic applies cleanly to a fresh SQLite file."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from db.database import build_sqlite_file_urls, run_alembic_upgrade


@pytest.fixture
def repo_root() -> Path:
    # tests/db/test_migrations.py → repo root
    return Path(__file__).resolve().parent.parent.parent


def test_alembic_upgrade_creates_users_and_runs_tables(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    sqlite_path = tmp_path / "dataloader.sqlite"
    sync_url, _ = build_sqlite_file_urls(sqlite_path)
    run_alembic_upgrade(repo_root, sync_url)

    con = sqlite3.connect(sqlite_path)
    try:
        users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        assert users >= 1
        names = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert "runs" in names
        assert "run_created_resources" in names
        assert "resource_correlation" not in names
        run_cols = {r[1] for r in con.execute("PRAGMA table_info(runs)")}
        assert "resources_created_count" in run_cols
        assert "resources_staged_count" in run_cols
        assert "resources_failed_count" in run_cols
        assert "config_json" in run_cols
        assert "run_extras_json" in run_cols
        assert "manifest_json" not in run_cols
    finally:
        con.close()
