"""Connection entity_id sync after reconciliation."""

from models import DataLoaderConfig
from org.discovery import DiscoveredConnection, DiscoveryResult
from org.reconciliation import (
    ReconciledResource,
    ReconciliationResult,
    sync_connection_entities_from_reconciliation,
)


def test_sync_updates_entity_id_from_discovered_vendor():
    raw = {
        "connections": [
            {"ref": "c1", "entity_id": "example1", "nickname": "N1"},
        ],
        "funds_flows": [],
    }
    config = DataLoaderConfig.model_validate(raw)
    discovery = DiscoveryResult(
        connections=[
            DiscoveredConnection(
                id="conn-uuid-1",
                vendor_name="Book",
                vendor_id="modern_treasury",
                currencies=["USD"],
            )
        ]
    )
    recon = ReconciliationResult(
        matches=[
            ReconciledResource(
                config_ref="connection.c1",
                config_resource=config.connections[0],
                discovered_id="conn-uuid-1",
                discovered_name="Book",
                match_reason="test",
                use_existing=True,
            )
        ]
    )
    sync_connection_entities_from_reconciliation(
        config,
        discovery,
        recon,
        {},
    )
    assert config.connections[0].entity_id == "modern_treasury"


def test_manual_map_connection_sets_entity_id():
    raw = {
        "connections": [
            {"ref": "c1", "entity_id": "example1", "nickname": "N1"},
        ],
        "funds_flows": [],
    }
    config = DataLoaderConfig.model_validate(raw)
    discovery = DiscoveryResult(
        connections=[
            DiscoveredConnection(
                id="id-b",
                vendor_name="B",
                vendor_id="example2",
                currencies=[],
            )
        ]
    )
    recon = ReconciliationResult(matches=[], unmatched_config=["connection.c1"])
    sync_connection_entities_from_reconciliation(
        config,
        discovery,
        recon,
        {"connection.c1": "id-b"},
    )
    assert config.connections[0].entity_id == "example2"
