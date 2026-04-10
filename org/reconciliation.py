"""Reconciliation — match config resources to discovered org resources.

Single-pass reconciliation that runs AFTER faker data injection so
resource names are fully resolved and matchable against the live org.
"""

from __future__ import annotations

from typing import Literal, cast

from loguru import logger

from models import DataLoaderConfig

from .discovery import DiscoveryResult
from .reconciliation_matchers import (
    ReconcileContext,
    append_catchall_unmatched_config_refs,
    append_unmatched_discovered,
    match_connections,
    match_counterparties,
    match_internal_accounts,
    match_ledger_account_categories,
    match_ledger_accounts,
    match_ledgers,
    match_legal_entities,
)
from .reconciliation_types import ReconciledResource, ReconciliationResult

__all__ = [
    "ReconciledResource",
    "ReconciliationResult",
    "reconcile_config",
    "sync_connection_entities_from_reconciliation",
]


def reconcile_config(
    config: DataLoaderConfig,
    discovery: DiscoveryResult,
) -> ReconciliationResult:
    """Match config-defined resources against discovered org resources.

    Must be called AFTER faker data has been injected (post
    ``generate_from_recipe``) so that resource names are fully resolved
    and matchable against the live org.

    Matching order: connections -> internal accounts -> ledgers ->
    ledger accounts -> ledger account categories -> legal entities ->
    counterparties.  All matchers use list-valued lookups for duplicate
    detection.
    """
    result = ReconciliationResult()
    ctx = ReconcileContext()

    match_connections(config, discovery, result, ctx)
    match_internal_accounts(config, discovery, result, ctx)
    match_ledgers(config, discovery, result, ctx)

    config_ledger_to_discovered: dict[str, str] = {
        m.config_ref: m.discovered_id for m in result.matches if m.config_ref.startswith("ledger.")
    }
    match_ledger_accounts(config, discovery, result, ctx, config_ledger_to_discovered)
    match_ledger_account_categories(config, discovery, result, ctx, config_ledger_to_discovered)
    match_legal_entities(config, discovery, result, ctx)
    match_counterparties(config, discovery, result, ctx)

    append_catchall_unmatched_config_refs(config, result)
    append_unmatched_discovered(discovery, result, ctx.matched_discovered_ids)

    logger.bind(
        matches=len(result.matches),
        unmatched_config=len(result.unmatched_config),
        unmatched_discovered=len(result.unmatched_discovered),
    ).info("Reconciliation complete")

    return result


_ALLOWED_CONNECTION_ENTITY_IDS = frozenset({"example1", "example2", "modern_treasury"})

_ConnectionEntityId = Literal["example1", "example2", "modern_treasury"]


def sync_connection_entities_from_reconciliation(
    config: DataLoaderConfig,
    discovery: DiscoveryResult,
    reconciliation: ReconciliationResult,
    manual_mappings: dict[str, str] | None = None,
) -> None:
    """Align ``connection.entity_id`` with each chosen MT connection's ``vendor_id``.

    Keeps JSON, drawer payloads, and execution aligned when reconciliation maps
    config connections to discovered org connections (including duplicate-picker
    and manual map flows).
    """
    maps = manual_mappings or {}
    by_id = {dc.id: dc for dc in discovery.connections}
    targets: dict[str, str] = {}

    for m in reconciliation.matches:
        if m.use_existing and m.config_ref.startswith("connection."):
            targets[m.config_ref] = m.discovered_id

    for cref, disc_id in maps.items():
        if cref.startswith("connection.") and disc_id:
            targets[cref] = disc_id

    conns = config.connections
    if not conns:
        return

    for tref, disc_id in targets.items():
        ref_key = tref.split(".", 1)[1] if "." in tref else ""
        if not ref_key:
            continue
        dc = by_id.get(disc_id)
        if dc is None:
            continue
        vid = (dc.vendor_id or "").strip()
        if vid not in _ALLOWED_CONNECTION_ENTITY_IDS:
            logger.warning(
                "Connection {}: skip entity_id sync — vendor_id {!r} not in {}",
                tref,
                vid,
                sorted(_ALLOWED_CONNECTION_ENTITY_IDS),
            )
            continue
        for i, conn in enumerate(conns):
            if conn.ref != ref_key:
                continue
            if conn.entity_id != vid:
                conns[i] = conn.model_copy(update={"entity_id": cast(_ConnectionEntityId, vid)})
            break
