"""Stable display/ref helpers for flow views (re-exported from compiler internals)."""

from __future__ import annotations

from .ir import _ref_account_type
from .mermaid import _build_ref_display_map, _resolve_actor_display

build_ref_display_map = _build_ref_display_map
ref_account_type = _ref_account_type
resolve_actor_display = _resolve_actor_display

__all__ = ["build_ref_display_map", "ref_account_type", "resolve_actor_display"]
