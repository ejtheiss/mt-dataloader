"""Legacy on-disk ``runs/<run_id>.json`` layout: discovery, path resolution, parse.

Used only by :mod:`dataloader.db_backfill` and one-off migration-style tooling —
not the DB-authoritative runtime path.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from jsonutil import loads_path

_RUN_ID_STEM = r"\d{8}T\d{6}_[0-9a-f]{8}"
_LEGACY_RUN_JSON_RE = re.compile(rf"^{_RUN_ID_STEM}\.json$")
_LEGACY_RUN_JSON_LEGACY_RE = re.compile(rf"^manifest_{_RUN_ID_STEM}\.json$")


def legacy_run_json_id_from_filename(filename: str) -> str | None:
    """Return run id if *filename* is a canonical or legacy ``manifest_<id>.json`` name."""
    if _LEGACY_RUN_JSON_RE.match(filename):
        return Path(filename).stem
    if _LEGACY_RUN_JSON_LEGACY_RE.match(filename):
        return Path(filename).stem.removeprefix("manifest_")
    return None


def list_legacy_run_json_ids(runs_dir: str | Path) -> list[str]:
    """Return run IDs from legacy per-run JSON files under *runs_dir*, newest first."""
    d = Path(runs_dir)
    if not d.exists():
        return []
    return [
        rid
        for p in sorted(d.glob("*.json"), reverse=True)
        if (rid := legacy_run_json_id_from_filename(p.name)) is not None
    ]


def resolve_legacy_run_json_path(runs_dir: str | Path, run_id: str) -> Path | None:
    """Return path to ``{run_id}.json`` / ``manifest_{run_id}.json``, or None."""
    d = Path(runs_dir)
    path = d / f"{run_id}.json"
    if path.is_file():
        return path
    path = d / f"manifest_{run_id}.json"
    if path.is_file():
        return path
    path = next(
        (p for p in d.glob("*.json") if p.stem == run_id or p.stem == f"manifest_{run_id}"),
        None,
    )
    if path is not None and path.is_file():
        return path
    return None


def load_legacy_run_json_dict(path: str | Path) -> dict[str, Any]:
    """Parse legacy run JSON; default ``run_id`` from filename stem if missing."""
    p = Path(path)
    data = loads_path(p)
    if not isinstance(data, dict):
        msg = f"legacy run JSON must be an object, got {type(data).__name__}"
        raise TypeError(msg)
    if not data.get("run_id"):
        data = {**data, "run_id": p.stem.replace("manifest_", "")}
    return data


class LegacyRunDiskSnapshot(BaseModel):
    """Typed view of historical ``runs/<run_id>.json`` for backfill / tests (``extra`` ignored)."""

    model_config = ConfigDict(extra="ignore")

    run_id: str
    config_hash: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    status: str = "running"
    resources_created: list[dict[str, Any]] = Field(default_factory=list)
    resources_failed: list[dict[str, Any]] = Field(default_factory=list)
    resources_staged: list[dict[str, Any]] = Field(default_factory=list)
    generation_recipe: dict[str, Any] | None = None
    compile_id: str | None = None
    seed_version: str | None = None
    mt_org_id: str | None = None
    mt_org_label: str | None = None
