"""Run manifest read paths with ``admin`` / ``user`` visibility (Plan 0)."""

from __future__ import annotations

from pathlib import Path

from fastapi import Request
from loguru import logger

from dataloader.engine import resolve_manifest_path
from db.repositories import runs as runs_repo
from db.repositories.runs import RunAccessContext
from models import AppSettings, CurrentAppUser, RunManifest


def user_to_ctx(user: CurrentAppUser) -> RunAccessContext:
    return RunAccessContext(user_id=user.id, is_admin=user.is_admin)


async def load_run_manifest_for_reader(
    request: Request,
    settings: AppSettings,
    run_id: str,
    user: CurrentAppUser,
) -> RunManifest | None:
    """DB manifest first, then disk — only if *user* may read this run (404 semantics)."""
    ctx = user_to_ctx(user)
    runs_dir = Path(settings.runs_dir)
    factory = getattr(request.app.state, "async_session_factory", None)
    row = None
    if factory is not None:
        try:
            async with factory() as session:
                row = await runs_repo.get_run_row_for_access(session, run_id, ctx)
        except Exception as exc:
            logger.bind(run_id=run_id).warning("db run access check failed: {}", exc)

    if factory is not None and row is not None and row.manifest_json:
        try:
            return RunManifest.model_validate_json(row.manifest_json)
        except Exception as exc:
            logger.bind(run_id=run_id).warning("db manifest parse failed, trying disk: {}", exc)

    if not ctx.is_admin and row is None:
        return None

    path = resolve_manifest_path(runs_dir, run_id)
    if path is None or not path.exists():
        return None
    try:
        return RunManifest.load(path)
    except Exception as exc:
        logger.bind(path=str(path), error=str(exc)).warning("Failed to load manifest")
        return None


async def run_is_readable(
    request: Request,
    settings: AppSettings,
    run_id: str,
    user: CurrentAppUser,
) -> bool:
    """Cheap ownership / visibility check without full manifest parse when possible."""
    ctx = user_to_ctx(user)
    runs_dir = Path(settings.runs_dir)
    factory = getattr(request.app.state, "async_session_factory", None)
    row = None
    if factory is not None:
        try:
            async with factory() as session:
                row = await runs_repo.get_run_row_for_access(session, run_id, ctx)
        except Exception as exc:
            logger.bind(run_id=run_id).warning("db run access check failed: {}", exc)

    if ctx.is_admin:
        if row is not None:
            return True
        path = resolve_manifest_path(runs_dir, run_id)
        return path is not None and path.exists()

    if row is None:
        return False
    if row.manifest_json:
        return True
    path = resolve_manifest_path(runs_dir, run_id)
    return path is not None and path.exists()
