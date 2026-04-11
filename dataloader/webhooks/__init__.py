"""Webhook domain package: routes, correlation state, and ``webhook_events`` persistence.

**02a** moved the former root ``webhooks.py`` under ``dataloader/webhooks/``; **07** split
``routes.py`` into composable sub-routers while keeping ``router`` stable for
``dataloader.main``. ``FIREABLE_TYPES`` lives in ``dataloader.staged_fire`` (shared with
the engine dry-run path); staged-fire HTTP handlers live in ``runs_staged.py``.
"""

from __future__ import annotations

from dataloader.staged_fire import FIREABLE_TYPES

from .correlation_state import (
    correlation_index_size,
    ensure_run_indexed_from_rows,
    index_resource,
    rebuild_correlation_index,
    register_run_org,
    replace_runtime_correlation_state,
)
from .routes import router
from .webhook_persist import enrich_webhooks_run_org

__all__ = [
    "FIREABLE_TYPES",
    "correlation_index_size",
    "enrich_webhooks_run_org",
    "ensure_run_indexed_from_rows",
    "index_resource",
    "rebuild_correlation_index",
    "register_run_org",
    "replace_runtime_correlation_state",
    "router",
]
