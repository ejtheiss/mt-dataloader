"""Run read paths with ``admin`` / ``user`` visibility (Plan 0) — DB-only."""

from __future__ import annotations

from fastapi import Request
from loguru import logger

from db.repositories import runs as runs_repo
from db.repositories.run_artifacts import fetch_run_detail_view
from db.repositories.runs import RunAccessContext
from models import AppSettings, CurrentAppUser, RunDetailView


def user_to_ctx(user: CurrentAppUser) -> RunAccessContext:
    return RunAccessContext(user_id=user.id, is_admin=user.is_admin)


async def get_run_detail_view(
    request: Request,
    settings: AppSettings,
    run_id: str,
    user: CurrentAppUser,
) -> RunDetailView | None:
    """Assemble run detail DTO from SQLite when *user* may read this run."""
    del settings  # DB-only; kept for call-site compatibility
    ctx = user_to_ctx(user)
    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is None:
        return None
    try:
        async with factory() as session:
            return await fetch_run_detail_view(session, run_id, ctx)
    except Exception as exc:
        logger.bind(run_id=run_id).warning("db run detail failed: {}", exc)
        return None


async def run_is_readable(
    request: Request,
    settings: AppSettings,
    run_id: str,
    user: CurrentAppUser,
) -> bool:
    """Ownership / visibility using ``runs`` row only (no disk)."""
    del settings
    ctx = user_to_ctx(user)
    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is None:
        return False
    try:
        async with factory() as session:
            row = await runs_repo.get_run_row_for_access(session, run_id, ctx)
    except Exception as exc:
        logger.bind(run_id=run_id).warning("db run access check failed: {}", exc)
        return False
    return row is not None
