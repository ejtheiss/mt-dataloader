"""Shared Pydantic error ``loc`` formatting (dotted path with index brackets)."""

from __future__ import annotations

from typing import Any


def format_pydantic_loc(loc: tuple[Any, ...]) -> str:
    """Join Pydantic ``ValidationError`` ``loc`` tuple into a dotted path."""
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{item}]"
            else:
                parts.append(str(item))
        else:
            parts.append(str(item))
    return ".".join(parts)
