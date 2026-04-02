"""Async engine factory, SQLite URLs, and Alembic upgrade helper."""

from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from alembic import command


def build_sqlite_file_urls(sqlite_file: Path) -> tuple[str, str]:
    """Return (sync_url, async_url) for the same on-disk SQLite file."""
    resolved = sqlite_file.resolve()
    path = resolved.as_posix()
    sync_url = f"sqlite:///{path}"
    async_url = f"sqlite+aiosqlite:///{path}"
    return sync_url, async_url


def run_alembic_upgrade(repo_root: Path, sync_sqlalchemy_url: str) -> None:
    """Run ``alembic upgrade head`` using *repo_root* as the Alembic script home."""
    root = repo_root.resolve()
    ini = root / "alembic.ini"
    cfg = Config()
    cfg.config_file_name = str(ini) if ini.is_file() else None
    cfg.set_main_option("script_location", str(root / "alembic"))
    cfg.set_main_option("prepend_sys_path", str(root))
    cfg.set_main_option("sqlalchemy.url", sync_sqlalchemy_url)
    command.upgrade(cfg, "head")


def create_async_engine_and_sessionmaker(async_sqlalchemy_url: str):
    """Create async engine with SQLite pragmas and an ``async_sessionmaker``."""
    engine = create_async_engine(async_sqlalchemy_url, pool_pre_ping=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, factory
