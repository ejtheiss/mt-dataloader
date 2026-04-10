"""Unit tests for individual reconciliation matchers (CH-3)."""

from __future__ import annotations

from models import DataLoaderConfig, LedgerConfig
from org.discovery import DiscoveredLedger, DiscoveryResult
from org.reconciliation_matchers import ReconcileContext, match_ledgers
from org.reconciliation_types import ReconciliationResult


def test_match_ledgers_pairs_by_name_case_insensitive():
    config = DataLoaderConfig(
        ledgers=[LedgerConfig(ref="ops", name="Operating Ledger")],
    )
    discovery = DiscoveryResult(
        ledgers=[DiscoveredLedger(id="uuid-ledger-1", name="operating ledger")],
    )
    result = ReconciliationResult()
    ctx = ReconcileContext()

    match_ledgers(config, discovery, result, ctx)

    assert len(result.matches) == 1
    assert result.matches[0].config_ref == "ledger.ops"
    assert result.matches[0].discovered_id == "uuid-ledger-1"
    assert "uuid-ledger-1" in ctx.matched_discovered_ids
    assert result.unmatched_config == []


def test_match_ledgers_unmatched_when_name_missing():
    config = DataLoaderConfig(
        ledgers=[LedgerConfig(ref="missing", name="Nowhere Ledger")],
    )
    discovery = DiscoveryResult(
        ledgers=[DiscoveredLedger(id="x", name="Other Name")],
    )
    result = ReconciliationResult()
    ctx = ReconcileContext()

    match_ledgers(config, discovery, result, ctx)

    assert result.matches == []
    assert "ledger.missing" in result.unmatched_config
