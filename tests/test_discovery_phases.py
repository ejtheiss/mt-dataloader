"""Tests for discovery helpers (CH-4)."""

from __future__ import annotations

from models import DataLoaderConfig, LegalEntityConfig
from org.discovery import resource_types_in_config


def test_resource_types_in_config_empty():
    assert resource_types_in_config(None) == set()


def test_resource_types_in_config_collects_resource_type():
    cfg = DataLoaderConfig(
        legal_entities=[
            LegalEntityConfig(
                ref="le1",
                legal_entity_type="business",
                business_name="Co",
            )
        ],
    )
    assert "legal_entity" in resource_types_in_config(cfg)
