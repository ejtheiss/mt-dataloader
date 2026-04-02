"""Runs listing routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from loguru import logger

from dataloader.engine import manifest_json_run_id, resolve_manifest_path
from dataloader.handlers import DELETABILITY
from dataloader.routers.deps import SettingsDep, TemplatesDep
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
    sort: str | None = None,
    dir: str = "asc",
    status: str | None = None,
    mt_org_id: str | None = None,
):
    """List runs: Wave B reads row metadata from SQLite; disk-only manifests fill gaps."""
    runs_dir = Path(settings.runs_dir)
    rows: list[RunListRow] = []
    seen: set[str] = set()
    factory = getattr(request.app.state, "async_session_factory", None)

    if factory is not None:
        try:
            async with factory() as session:
                rows = await runs_repo.list_run_rows_for_api(session)
            seen = {r.run_id for r in rows}
        except Exception as exc:
            logger.warning("db list_run_rows_for_api failed, using disk-only listing: {}", exc)

    if runs_dir.exists():
        for path in sorted(runs_dir.glob("*.json"), reverse=True):
            if manifest_json_run_id(path.name) is None:
                continue
            try:
                m = RunManifest.load(path)
            except Exception as e:
                logger.bind(path=str(path), error=str(e)).warning("Failed to load manifest")
                continue
            if m.run_id in seen:
                continue
            rows.append(RunListRow.from_manifest(m))

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
        "runs.html",
        {
            "manifests": rows,
            "deletability": DELETABILITY,
            "sort_key": sort or "",
            "sort_dir": dir,
            "active_status": status or "",
        },
    )


@router.get("/api/runs/{run_id}/drawer")
async def run_drawer(
    request: Request,
    run_id: str,
    templates: TemplatesDep,
    settings: SettingsDep,
):
    """Return drawer partial for a single run."""
    runs_dir = Path(settings.runs_dir)
    path = resolve_manifest_path(runs_dir, run_id)
    if path is None or not path.exists():
        return templates.TemplateResponse(
            request,
            "partials/empty_state.html",
            {"empty_title": "Run not found", "empty_description": f"No manifest for {run_id}"},
        )
    manifest = RunManifest.load(path)
    return templates.TemplateResponse(request, "partials/run_drawer.html", {"manifest": manifest})


@router.get("/api/runs/{run_id}/resources/drawer")
async def resource_drawer_in_run(
    request: Request,
    run_id: str,
    templates: TemplatesDep,
    settings: SettingsDep,
    ref: str = "",
):
    """Return drawer partial for a single resource within a run."""
    path = resolve_manifest_path(Path(settings.runs_dir), run_id)
    if path is None or not path.exists():
        return templates.TemplateResponse(
            request,
            "partials/empty_state.html",
            {"empty_title": "Run not found"},
        )
    manifest = RunManifest.load(path)
    entry = next((e for e in manifest.resources_created if e.typed_ref == ref), None)
    if not entry:
        return templates.TemplateResponse(
            request,
            "partials/empty_state.html",
            {"empty_title": "Resource not found", "empty_description": ref},
        )
    item = entry
    return templates.TemplateResponse(
        request,
        "partials/resource_drawer.html",
        {"item": item},
    )
