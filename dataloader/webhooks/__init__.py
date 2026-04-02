"""Webhook domain package: routes, correlation index, JSONL persistence.

Phase **02a** moved the former root ``webhooks.py`` into ``dataloader/webhooks/routes.py``;
this module re-exports the public API so ``dataloader.main``, ``engine``, and
``dataloader.routers.execute`` keep stable imports.
"""

from __future__ import annotations

from .routes import (
    FIREABLE_TYPES,
    build_run_org_map,
    enrich_webhooks_run_org,
    ensure_run_indexed,
    index_resource,
    load_webhooks,
    rebuild_correlation_index,
    register_run_org,
    router,
)

__all__ = [
    "FIREABLE_TYPES",
    "build_run_org_map",
    "enrich_webhooks_run_org",
    "ensure_run_indexed",
    "index_resource",
    "load_webhooks",
    "rebuild_correlation_index",
    "register_run_org",
    "router",
]
