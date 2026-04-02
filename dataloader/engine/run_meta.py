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


_MANIFEST_RE = re.compile(r"^\d{8}T\d{6}_[0-9a-f]{8}\.json$")


def list_manifest_ids(runs_dir: str | Path) -> list[str]:
    """Return run IDs from manifest files, newest first."""
    d = Path(runs_dir)
    if not d.exists():
        return []
    return [p.stem for p in sorted(d.glob("*.json"), reverse=True) if _MANIFEST_RE.match(p.name)]
