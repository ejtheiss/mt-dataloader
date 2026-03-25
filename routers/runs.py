"""Runs listing routes."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from loguru import logger

from engine import RunManifest, _MANIFEST_RE
from handlers import DELETABILITY
from helpers import get_templates

router = APIRouter(tags=["runs"])


@router.get("/runs", include_in_schema=False)
async def runs_page(request: Request):
    templates = get_templates()
    return templates.TemplateResponse(request, "runs_page.html", {"title": "Runs"})


@router.get("/api/runs")
async def list_runs(
    request: Request,
    sort: str | None = None,
    dir: str = "asc",
    status: str | None = None,
):
    """List past run manifests with optional sort and filter."""
    templates = get_templates()
    runs_dir = Path(request.app.state.settings.runs_dir)
    manifests: list[RunManifest] = []
    if runs_dir.exists():
        for path in sorted(runs_dir.glob("*.json"), reverse=True):
            if not _MANIFEST_RE.match(path.name):
                continue
            try:
                manifests.append(RunManifest.load(path))
            except Exception as e:
                logger.bind(path=str(path), error=str(e)).warning(
                    "Failed to load manifest"
                )

    if status:
        manifests = [m for m in manifests if str(m.status) == status or getattr(m.status, "value", None) == status]

    sort_keys = {
        "run_id": lambda m: m.run_id,
        "status": lambda m: str(getattr(m.status, "value", m.status)),
        "resources": lambda m: len(m.resources_created),
        "staged": lambda m: len(m.resources_staged) if m.resources_staged else 0,
        "failed": lambda m: len(m.resources_failed) if m.resources_failed else 0,
    }
    if sort and sort in sort_keys:
        manifests.sort(key=sort_keys[sort], reverse=(dir == "desc"))

    return templates.TemplateResponse(
        request,
        "runs.html",
        {
            "manifests": manifests,
            "deletability": DELETABILITY,
            "sort_key": sort or "",
            "sort_dir": dir,
            "active_status": status or "",
        },
    )


@router.get("/api/runs/{run_id}/drawer")
async def run_drawer(request: Request, run_id: str):
    """Return drawer partial for a single run."""
    templates = get_templates()
    runs_dir = Path(request.app.state.settings.runs_dir)
    path = runs_dir / f"manifest_{run_id}.json"
    if not path.exists():
        path = next(
            (p for p in runs_dir.glob("*.json") if run_id in p.name), None
        )
    if not path or not path.exists():
        return templates.TemplateResponse(
            request,
            "partials/empty_state.html",
            {"empty_title": "Run not found", "empty_description": f"No manifest for {run_id}"},
        )
    manifest = RunManifest.load(path)
    return templates.TemplateResponse(
        request, "partials/run_drawer.html", {"manifest": manifest}
    )


def _find_manifest(request: Request, run_id: str) -> RunManifest | None:
    runs_dir = Path(request.app.state.settings.runs_dir)
    path = runs_dir / f"manifest_{run_id}.json"
    if not path.exists():
        path = next(
            (p for p in runs_dir.glob("*.json") if run_id in p.name), None
        )
    if not path or not path.exists():
        return None
    return RunManifest.load(path)


@router.get("/api/runs/{run_id}/resources/drawer")
async def resource_drawer_in_run(request: Request, run_id: str, ref: str = ""):
    """Return drawer partial for a single resource within a run."""
    templates = get_templates()
    manifest = _find_manifest(request, run_id)
    if not manifest:
        return templates.TemplateResponse(
            request,
            "partials/empty_state.html",
            {"empty_title": "Run not found"},
        )
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
