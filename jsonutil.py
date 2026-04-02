"""Central JSON encode/decode helpers — one place for indent and default=str."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def dumps_pretty(obj: Any) -> str:
    """Serialize *obj* to indented JSON; non-JSON-native values use str()."""
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def loads_path(path: str | Path, *, encoding: str = "utf-8") -> Any:
    """Parse JSON from a filesystem path."""
    return json.loads(Path(path).read_text(encoding=encoding))


def loads_str(s: str) -> Any:
    """Parse JSON from a string (stdlib policy lives in this module)."""
    return json.loads(s)


def dumps_jsonl_record(obj: Any) -> str:
    """Serialize one JSONL record (no newline). Compact, ``default=str``."""
    return json.dumps(obj, ensure_ascii=False, default=str, separators=(",", ":"))
