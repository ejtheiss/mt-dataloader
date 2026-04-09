"""Runs listing routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from dataloader.engine import manifest_json_run_id
from dataloader.routers.deps import CurrentAppUserDep, SettingsDep, TemplatesDep
from dataloader.view_models import runs_list_fragment_context
from dataloader.run_access import load_run_manifest_for_reader, user_to_ctx
from db.repositories import runs as runs_repo
from models import RunListRow, RunManifest

router = APIRouter(tags=["runs"])


@router.get("/runs", include_in_schema=False)
async def runs_page(request: Request, templates: TemplatesDep):
    return templates.TemplateResponse(request, "runs_page.html", {"title": "Runs"})


@router.get("/api/runs")
async def list_runs(
    request: Request,
    templates: TemplatesDep,
    settings: SettingsDep,
    current_user: CurrentAppUserDep,
    sort: str | None = None,
    dir: str = "asc",
    status: str | None = None,
    mt_org_id: str | None = None,
):
    """List runs: Wave B uses SQLite when DB is up; ``user`` sees only owned rows."""
    runs_dir = Path(settings.runs_dir)
    rows: list[RunListRow] = []
    db_list_ok = False
    factory = getattr(request.app.state, "async_session_factory", None)
    ctx = user_to_ctx(current_user)

    if factory is not None:
        try:
            async with factory() as session:
                rows = await runs_repo.list_run_rows_for_api(session, ctx)
            db_list_ok = True
        except Exception as exc:
            logger.warning("db list_run_rows_for_api failed, using disk-only listing: {}", exc)

    if not db_list_ok and runs_dir.exists():
        if current_user.is_admin:
            for path in sorted(runs_dir.glob("*.json"), reverse=True):
                if manifest_json_run_id(path.name) is None:
                    continue
                try:
                    m = RunManifest.load(path)
                except Exception as e:
                    logger.bind(path=str(path), error=str(e)).warning("Failed to load manifest")
                    continue
                rows.append(RunListRow.from_manifest(m))
        else:
            logger.warning(
                "DB unavailable — user role sees empty runs list (disk glob disabled for isolation)"
            )

    if status:
        rows = [r for r in rows if r.status == status]

    if mt_org_id and mt_org_id.strip():
        oid = mt_org_id.strip()
        rows = [r for r in rows if r.mt_org_id == oid]

    sort_keys = {
        "run_id": lambda r: r.run_id,
        "status": lambda r: r.status,
        "resources": lambda r: r.resource_count,
        "staged": lambda r: r.staged_count,
        "failed": lambda r: r.failed_count,
    }
    if sort and sort in sort_keys:
        rows.sort(key=sort_keys[sort], reverse=(dir == "desc"))
    else:
        rows.sort(key=lambda r: r.started_at, reverse=True)

    return templates.TemplateResponse(
        request,
        "runs_page.html",
        runs_list_fragment_context(rows=rows, sort=sort, dir=dir, status=status),
        block_name="runs_list",
    )


@router.get("/api/runs/{run_id}/drawer")
async def run_drawer(
    request: Request,
    run_id: str,
    templates: TemplatesDep,
    settings: SettingsDep,
    current_user: CurrentAppUserDep,
):
    """Return drawer partial for a single run."""
    manifest = await load_run_manifest_for_reader(request, settings, run_id, current_user)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return templates.TemplateResponse(request, "partials/run_drawer.html", {"manifest": manifest})


@router.get("/api/runs/{run_id}/resources/drawer")
async def resource_drawer_in_run(
    request: Request,
    run_id: str,
    templates: TemplatesDep,
    settings: SettingsDep,
    current_user: CurrentAppUserDep,
    ref: str = "",
):
    """Return drawer partial for a single resource within a run."""
    manifest = await load_run_manifest_for_reader(request, settings, run_id, current_user)
    if manifest is None:
        raise HTTPException(status_code=404, detail="Run not found")
    entry = next((e for e in manifest.resources_created if e.typed_ref == ref), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Resource not found")
    item = entry
    return templates.TemplateResponse(
        request,
        "partials/resource_drawer.html",
        {"item": item},
    )
