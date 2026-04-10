"""Setup helpers: HTMX reconcile_overrides string parsing."""

from __future__ import annotations

from dataloader.routers.setup._helpers import reconcile_pairs_from_json_string


def test_empty_and_whitespace() -> None:
    assert reconcile_pairs_from_json_string(None) == ({}, {})
    assert reconcile_pairs_from_json_string("") == ({}, {})
    assert reconcile_pairs_from_json_string("   ") == ({}, {})


def test_invalid_json_returns_empty() -> None:
    assert reconcile_pairs_from_json_string("not json") == ({}, {})


def test_nested_overrides_and_manual_mappings() -> None:
    raw = '{"overrides": {"a.b": true}, "manual_mappings": {"c.d": "uuid-1"}}'
    o, m = reconcile_pairs_from_json_string(raw)
    assert o == {"a.b": True}
    assert m == {"c.d": "uuid-1"}


def test_flat_dict_becomes_overrides() -> None:
    raw = '{"ledger_account.x": false}'
    o, m = reconcile_pairs_from_json_string(raw)
    assert o == {"ledger_account.x": False}
    assert m == {}
