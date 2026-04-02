"""Shared handler metadata and Tenacity presets (also used by staged webhook fire)."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from tenacity import stop_after_delay, wait_exponential

from models import HandlerResult

EmitFn = Callable[[str, str, dict[str, Any]], Awaitable[None]]
HandlerFn = Callable[..., Awaitable[HandlerResult]]
UpdateHandlerFn = Callable[..., Awaitable[HandlerResult]]

TENACITY_WAIT_EXP_2_10 = wait_exponential(multiplier=1, min=2, max=10)
TENACITY_WAIT_EXP_2_5 = wait_exponential(multiplier=1, min=2, max=5)
TENACITY_STOP_30 = stop_after_delay(30)
TENACITY_STOP_60 = stop_after_delay(60)

DELETABILITY: dict[str, bool] = {
    "connection": False,
    "legal_entity": False,
    "legal_entity_association": False,
    "ledger": True,
    "counterparty": True,
    "ledger_account": True,
    "internal_account": False,
    "external_account": True,
    "ledger_account_category": True,
    "ledger_account_settlement": False,
    "ledger_account_balance_monitor": True,
    "ledger_account_statement": False,
    "virtual_account": True,
    "expected_payment": True,
    "payment_order": False,
    "incoming_payment_detail": False,
    "ledger_transaction": False,
    "transaction": False,
    "return": False,
    "reversal": False,
    "category_membership": True,
    "nested_category": True,
    "transition_ledger_transaction": False,
    "verify_external_account": False,
    "complete_verification": False,
    "archive_resource": False,
}

SDK_ATTR_MAP: dict[str, str] = {
    "connection": "connections",
    "legal_entity": "legal_entities",
    "ledger": "ledgers",
    "counterparty": "counterparties",
    "ledger_account": "ledger_accounts",
    "internal_account": "internal_accounts",
    "external_account": "external_accounts",
    "ledger_account_category": "ledger_account_categories",
    "virtual_account": "virtual_accounts",
    "expected_payment": "expected_payments",
    "payment_order": "payment_orders",
    "incoming_payment_detail": "incoming_payment_details",
    "ledger_transaction": "ledger_transactions",
    "return": "returns",
    "reversal": "payment_orders",
    "ledger_account_settlement": "ledger_account_settlements",
    "ledger_account_balance_monitor": "ledger_account_balance_monitors",
    "ledger_account_statement": "ledger_account_statements",
    "transaction": "transactions",
}
