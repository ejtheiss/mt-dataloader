"""Run IDs, config hashing, and timestamps for execution."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone

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
