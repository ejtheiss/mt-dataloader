"""Internal-account currency vs connection rail (Pydantic)."""

import pytest

from models import DataLoaderConfig


def test_usdg_connection_requires_usdg_currency():
    raw = {
        "connections": [
            {"ref": "psp_usdg", "entity_id": "example1", "nickname": "USDG"},
        ],
        "internal_accounts": [
            {
                "ref": "ia_pool",
                "connection_id": "$ref:connection.psp_usdg",
                "legal_entity_id": "$ref:legal_entity.le1",
                "name": "Pool",
                "party_name": "Co",
                "currency": "USD",
            },
        ],
        "legal_entities": [
            {"ref": "le1", "legal_entity_type": "business", "business_name": "Co"},
        ],
        "funds_flows": [],
    }
    with pytest.raises(ValueError, match="incompatible with connection"):
        DataLoaderConfig.model_validate(raw)


def test_usd_psp_connection_requires_usd_currency():
    raw = {
        "connections": [
            {"ref": "co_psp_usd", "entity_id": "example1", "nickname": "USD"},
        ],
        "internal_accounts": [
            {
                "ref": "ia1",
                "connection_id": "$ref:connection.co_psp_usd",
                "legal_entity_id": "$ref:legal_entity.le1",
                "name": "A",
                "party_name": "Co",
                "currency": "USDG",
            },
        ],
        "legal_entities": [
            {"ref": "le1", "legal_entity_type": "business", "business_name": "Co"},
        ],
        "funds_flows": [],
    }
    with pytest.raises(ValueError, match="incompatible with connection"):
        DataLoaderConfig.model_validate(raw)


def test_agnostic_connection_allows_mixed_currencies():
    raw = {
        "connections": [
            {"ref": "platform_psp", "entity_id": "modern_treasury", "nickname": "Book"},
        ],
        "internal_accounts": [
            {
                "ref": "ia_usd",
                "connection_id": "$ref:connection.platform_psp",
                "legal_entity_id": "$ref:legal_entity.le1",
                "name": "U",
                "party_name": "Co",
                "currency": "USD",
            },
            {
                "ref": "ia_usdc",
                "connection_id": "$ref:connection.platform_psp",
                "legal_entity_id": "$ref:legal_entity.le1",
                "name": "C",
                "party_name": "Co",
                "currency": "USDC",
            },
        ],
        "legal_entities": [
            {"ref": "le1", "legal_entity_type": "business", "business_name": "Co"},
        ],
        "funds_flows": [],
    }
    cfg = DataLoaderConfig.model_validate(raw)
    assert len(cfg.internal_accounts) == 2
