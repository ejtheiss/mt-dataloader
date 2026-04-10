"""Ledger currency_exponent defaults for MT non-ISO currencies (USDC, USDG)."""

from __future__ import annotations

from dataloader.engine.refs import RefRegistry, resolve_refs
from models import InlineLedgerAccountConfig, LedgerAccountCategoryConfig, LedgerAccountConfig


def _ledger_registry() -> RefRegistry:
    reg = RefRegistry()
    reg.register("ledger.main", "11111111-1111-1111-1111-111111111111")
    return reg


def test_usdg_ledger_account_gets_default_exponent() -> None:
    la = LedgerAccountConfig(
        ref="platform_usdg_pool",
        name="Platform USDG Pool",
        ledger_id="$ref:ledger.main",
        normal_balance="debit",
        currency="USDG",
    )
    assert la.currency_exponent == 2


def test_usdc_ledger_account_category_gets_default_exponent() -> None:
    cat = LedgerAccountCategoryConfig(
        ref="cat_usdc",
        name="USDC Cat",
        ledger_id="$ref:ledger.main",
        normal_balance="credit",
        currency="USDC",
    )
    assert cat.currency_exponent == 2


def test_usd_ledger_account_omits_exponent_from_payload() -> None:
    la = LedgerAccountConfig(
        ref="cash",
        name="Cash",
        ledger_id="$ref:ledger.main",
        normal_balance="debit",
        currency="USD",
    )
    assert la.currency_exponent is None
    payload = resolve_refs(la, _ledger_registry())
    assert "currency_exponent" not in payload


def test_resolve_refs_includes_exponent_for_usdg() -> None:
    la = LedgerAccountConfig(
        ref="platform_usdg_pool",
        name="Platform USDG Pool",
        ledger_id="$ref:ledger.main",
        normal_balance="debit",
        currency="USDG",
    )
    payload = resolve_refs(la, _ledger_registry())
    assert payload["currency"] == "USDG"
    assert payload["currency_exponent"] == 2


def test_explicit_currency_exponent_not_overridden() -> None:
    la = LedgerAccountConfig(
        ref="custom",
        name="Custom",
        ledger_id="$ref:ledger.main",
        normal_balance="credit",
        currency="USDG",
        currency_exponent=6,
    )
    assert la.currency_exponent == 6
    payload = resolve_refs(la, _ledger_registry())
    assert payload["currency_exponent"] == 6


def test_inline_ledger_account_usdg_gets_exponent() -> None:
    inline = InlineLedgerAccountConfig(
        name="Inline USDG",
        normal_balance="debit",
        currency="USDG",
    )
    assert inline.currency_exponent == 2
