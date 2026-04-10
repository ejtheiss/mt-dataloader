"""Dashboard deep links for created resource ids."""

from __future__ import annotations

from dataloader.mt_app_links import MT_APP_BASE, mt_app_resource_url


def test_mt_app_resource_url_expected_payment() -> None:
    u = mt_app_resource_url("expected_payment", "ep_abc123")
    assert u == f"{MT_APP_BASE}/expected_payments/ep_abc123"


def test_mt_app_resource_url_skipped_and_empty() -> None:
    assert mt_app_resource_url("ledger", "") is None
    assert mt_app_resource_url("ledger", "SKIPPED") is None


def test_mt_app_resource_url_reversal_not_linked() -> None:
    assert mt_app_resource_url("reversal", "rv_123") is None
