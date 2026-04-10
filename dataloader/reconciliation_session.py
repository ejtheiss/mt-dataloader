"""Apply org reconciliation to a loader session after config generation."""

from __future__ import annotations

from typing import Any

from models import DataLoaderConfig
from org.discovery import DiscoveryResult
from org.reconciliation import reconcile_config, sync_connection_entities_from_reconciliation


def apply_reconciliation_to_session(
    session: Any,
    config: DataLoaderConfig,
    discovery: DiscoveryResult,
) -> None:
    reconciliation = reconcile_config(config, discovery)
    skip_refs: set[str] = set()
    for m in reconciliation.matches:
        if m.use_existing:
            session.registry.register_or_update(m.config_ref, m.discovered_id)
            skip_refs.add(m.config_ref)
            for ck, cid in m.child_refs.items():
                session.registry.register_or_update(f"{m.config_ref}.{ck}", cid)
    session.reconciliation = reconciliation
    session.skip_refs = skip_refs
    sync_connection_entities_from_reconciliation(
        config,
        discovery,
        reconciliation,
        {},
    )
