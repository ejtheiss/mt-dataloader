"""Webhook domain package: routes, correlation state, and ``webhook_events`` persistence.

Phase **02a** moved the former root ``webhooks.py`` into ``dataloader.webhooks.routes.py``;
this module re-exports the public API so ``dataloader.main`` and
``dataloader.routers.execute`` keep stable imports. ``FIREABLE_TYPES`` is defined in
``dataloader.staged_fire`` (shared with the engine dry-run path).
"""

from __future__ import annotations

from dataloader.staged_fire import FIREABLE_TYPES

from .correlation_state import (
    correlation_index_size,
    ensure_run_indexed,
    index_resource,
    rebuild_correlation_index,
    register_run_org,
    replace_runtime_correlation_state,
)
from .routes import enrich_webhooks_run_org, router

__all__ = [
    "FIREABLE_TYPES",
    "correlation_index_size",
    "enrich_webhooks_run_org",
    "ensure_run_indexed",
    "index_resource",
    "rebuild_correlation_index",
    "register_run_org",
    "replace_runtime_correlation_state",
    "router",
]
