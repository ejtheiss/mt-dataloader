"""Run IDs, config hashing, manifest filename pattern, listing."""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path

from models import DataLoaderConfig


def generate_run_id() -> str:
    """``YYYYMMDDTHHMMSS_<8-char-hex>``, filesystem-safe."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    suffix = secrets.token_hex(4)
    return f"{ts}_{suffix}"


def config_hash(config: DataLoaderConfig) -> str:
    """SHA-256 of the canonical JSON serialization of the config."""
    canonical = config.model_dump_json(exclude_none=True)
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_RUN_ID_STEM = r"\d{8}T\d{6}_[0-9a-f]{8}"
_MANIFEST_RE = re.compile(rf"^{_RUN_ID_STEM}\.json$")
_MANIFEST_LEGACY_RE = re.compile(rf"^manifest_{_RUN_ID_STEM}\.json$")


def manifest_json_run_id(filename: str) -> str | None:
    """Return run id if ``filename`` is a canonical or legacy manifest JSON name."""
    if _MANIFEST_RE.match(filename):
        return Path(filename).stem
    if _MANIFEST_LEGACY_RE.match(filename):
        return Path(filename).stem.removeprefix("manifest_")
    return None


def list_manifest_ids(runs_dir: str | Path) -> list[str]:
    """Return run IDs from manifest files, newest first."""
    d = Path(runs_dir)
    if not d.exists():
        return []
    return [
        rid
        for p in sorted(d.glob("*.json"), reverse=True)
        if (rid := manifest_json_run_id(p.name)) is not None
    ]


def resolve_manifest_path(runs_dir: str | Path, run_id: str) -> Path | None:
    """Return path to a manifest JSON for ``run_id``, or None if not found."""
    d = Path(runs_dir)
    path = d / f"{run_id}.json"
    if path.is_file():
        return path
    path = d / f"manifest_{run_id}.json"
    if path.is_file():
        return path
    path = next(
        (
            p
            for p in d.glob("*.json")
            if p.stem == run_id or p.stem == f"manifest_{run_id}"
        ),
        None,
    )
    if path is not None and path.is_file():
        return path
    return None
