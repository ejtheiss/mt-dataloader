"""Org discovery, reconciliation, and registry for the Modern Treasury Dataloader.

Replaces the deprecated baseline.py module. Three concerns:

1. **Discovery** — dynamically discover connections, IAs, ledgers, LEs, CPs
   from a live MT org.
2. **Registry** — Pydantic model mapping discovered typed_refs to live UUIDs,
   bridges discovery data into the engine's mutable RefRegistry.
3. **Reconciliation** — match config-defined resources against discovered org
   resources. Runs AFTER faker data injection so resource names are real.
"""

from .discovery import (
    DiscoveredConnection,
    DiscoveredCounterparty,
    DiscoveredInternalAccount,
    DiscoveredLedger,
    DiscoveredLedgerAccount,
    DiscoveredLedgerAccountCategory,
    DiscoveredLegalEntity,
    DiscoveryResult,
    _le_display_name,
    _le_display_name_from_sdk,
    discover_org,
)
from .reconciliation import (
    ReconciledResource,
    ReconciliationResult,
    reconcile_config,
)
from .registry import OrgRegistry

__all__ = [
    # Discovery
    "DiscoveredConnection",
    "DiscoveredCounterparty",
    "DiscoveredInternalAccount",
    "DiscoveredLedger",
    "DiscoveredLedgerAccount",
    "DiscoveredLedgerAccountCategory",
    "DiscoveredLegalEntity",
    "DiscoveryResult",
    "_le_display_name",
    "_le_display_name_from_sdk",
    "discover_org",
    # Reconciliation
    "ReconciledResource",
    "ReconciliationResult",
    "reconcile_config",
    # Registry
    "OrgRegistry",
]
