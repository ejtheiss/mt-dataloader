"""Opaque cursor helpers for keyset pagination (Plan 09 — default ``started_at`` sort only)."""

from __future__ import annotations

import base64
import binascii
import json


class RunsSeekCursorError(ValueError):
    """Invalid or unsupported seek cursor payload."""


def encode_runs_seek_cursor(started_at: str, run_id: str) -> str:
    """Encode ``(started_at, run_id)`` for ``ORDER BY started_at DESC, run_id DESC`` paging."""
    payload = json.dumps({"v": 1, "sa": started_at, "rid": run_id}, separators=(",", ":"))
    raw = base64.urlsafe_b64encode(payload.encode())
    return raw.decode().rstrip("=")


def decode_runs_seek_cursor(token: str) -> tuple[str, str]:
    """Decode cursor from :func:`encode_runs_seek_cursor`; raises :exc:`RunsSeekCursorError` if invalid."""
    t = token.strip()
    if not t:
        raise RunsSeekCursorError("empty cursor")
    pad = "=" * (-len(t) % 4)
    try:
        decoded = base64.urlsafe_b64decode(t + pad)
        data = json.loads(decoded.decode())
    except (json.JSONDecodeError, UnicodeDecodeError, binascii.Error) as exc:
        raise RunsSeekCursorError("malformed cursor") from exc
    if not isinstance(data, dict) or data.get("v") != 1:
        raise RunsSeekCursorError("unsupported cursor version")
    sa = data.get("sa")
    rid = data.get("rid")
    if not isinstance(sa, str) or not isinstance(rid, str) or not sa or not rid:
        raise RunsSeekCursorError("cursor missing started_at or run_id")
    return sa, rid
