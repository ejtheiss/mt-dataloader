"""RFC 6901 JSON Pointer helpers for Plan 05 config patch (set only, no jsonpatch dep)."""

from __future__ import annotations

from typing import Any


def _decode_token(tok: str) -> str:
    return tok.replace("~1", "/").replace("~0", "~")


def _tokens(pointer: str) -> list[str]:
    if pointer == "":
        return []
    if not pointer.startswith("/"):
        raise ValueError("JSON Pointer must be empty or start with '/'")
    raw = pointer[1:].split("/")
    return [_decode_token(t) for t in raw]


def apply_json_pointer_set(doc: dict[str, Any], pointer: str, value: Any) -> None:
    """Set ``value`` at ``pointer`` (RFC 6901). ``doc`` must be the root object (dict).

    Creates missing ``dict`` parents; for array indices, parent list must exist and index
    must be in range or equal to ``len`` for append (only if intermediate already list).
    """
    parts = _tokens(pointer)
    if not parts:
        raise ValueError("cannot set document root via pointer; use shallow_merge")

    cur: Any = doc
    for i, key in enumerate(parts[:-1]):
        nxt = parts[i + 1]
        is_next_index = nxt.isdigit()
        if isinstance(cur, dict):
            if key not in cur or cur[key] is None:
                cur[key] = [] if is_next_index else {}
            cur = cur[key]
        elif isinstance(cur, list):
            idx = int(key)
            if idx < 0 or idx >= len(cur):
                raise KeyError(f"list index out of range at segment {key!r}")
            cur = cur[idx]
        else:
            raise TypeError(f"cannot traverse into {type(cur).__name__} at {key!r}")

    last = parts[-1]
    if isinstance(cur, dict):
        cur[last] = value
        return
    if isinstance(cur, list):
        idx = int(last) if last.isdigit() else -1
        if idx < 0:
            raise ValueError("array path must end with numeric token")
        if idx > len(cur):
            raise KeyError("list append only at len() index")
        if idx == len(cur):
            cur.append(value)
        else:
            cur[idx] = value
        return
    raise TypeError(f"parent is not dict or list at terminal {last!r}")
