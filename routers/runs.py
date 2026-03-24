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
async def list_runs(request: Request):
    """List past run manifests."""
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

    return templates.TemplateResponse(
        request,
        "runs.html",
        {"manifests": manifests, "deletability": DELETABILITY},
    )
