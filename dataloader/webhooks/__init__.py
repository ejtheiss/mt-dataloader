"""Webhook domain package: routes, correlation index, JSONL persistence.

Phase **02a** moved the former root ``webhooks.py`` into ``dataloader/webhooks/routes.py``;
this module re-exports the public API so ``dataloader.main`` and
``dataloader.routers.execute`` keep stable imports. ``FIREABLE_TYPES`` is defined in
``dataloader.staged_fire`` (shared with the engine dry-run path).
"""

from __future__ import annotations

from dataloader.staged_fire import FIREABLE_TYPES

from .routes import (
    build_run_org_map,
    correlation_index_size,
    enrich_webhooks_run_org,
    ensure_run_indexed,
    index_resource,
    load_webhooks,
    rebuild_correlation_index,
    recorrelate_unmatched_webhooks,
    register_run_org,
    replace_runtime_correlation_state,
    router,
)

__all__ = [
    "FIREABLE_TYPES",
    "build_run_org_map",
    "correlation_index_size",
    "enrich_webhooks_run_org",
    "ensure_run_indexed",
    "index_resource",
    "load_webhooks",
    "rebuild_correlation_index",
    "recorrelate_unmatched_webhooks",
    "register_run_org",
    "replace_runtime_correlation_state",
    "router",
]
