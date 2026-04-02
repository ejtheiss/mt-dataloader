"""Async handler functions for Modern Treasury SDK resource creation.

Each handler:
1. Receives a resolved dict (all $ref: strings replaced with UUIDs)
2. Calls the corresponding AsyncModernTreasury SDK method
3. Returns a HandlerResult with created ID, child refs, and deletability

This is the ONLY package that imports the MT SDK for resource CRUD.
Implementation lives in ``constants``, ``operations``, and ``dispatch`` submodules.
"""

from __future__ import annotations

from .constants import (
    DELETABILITY,
    SDK_ATTR_MAP,
    TENACITY_STOP_30,
    TENACITY_STOP_60,
    TENACITY_WAIT_EXP_2_5,
    TENACITY_WAIT_EXP_2_10,
)
from .dispatch import build_handler_dispatch, build_update_dispatch
from .operations import (
    archive_resource,
    complete_verification,
    create_category_membership,
    create_connection,
    create_counterparty,
    create_expected_payment,
    create_external_account,
    create_incoming_payment_detail,
    create_internal_account,
    create_ledger,
    create_ledger_account,
    create_ledger_account_balance_monitor,
    create_ledger_account_category,
    create_ledger_account_settlement,
    create_ledger_account_statement,
    create_ledger_transaction,
    create_legal_entity,
    create_legal_entity_association,
    create_nested_category,
    create_payment_order,
    create_return,
    create_reversal,
    create_transaction,
    create_virtual_account,
    list_resources,
    read_resource,
    transition_ledger_transaction,
    verify_external_account,
)

__all__ = [
    "DELETABILITY",
    "SDK_ATTR_MAP",
    "build_handler_dispatch",
    "build_update_dispatch",
    "create_connection",
    "create_legal_entity",
    "create_ledger",
    "create_counterparty",
    "create_ledger_account",
    "create_internal_account",
    "create_external_account",
    "create_ledger_account_category",
    "create_virtual_account",
    "create_expected_payment",
    "create_payment_order",
    "create_incoming_payment_detail",
    "create_ledger_transaction",
    "create_return",
    "create_reversal",
    "create_category_membership",
    "create_nested_category",
    "transition_ledger_transaction",
    "create_ledger_account_settlement",
    "create_ledger_account_balance_monitor",
    "create_ledger_account_statement",
    "create_legal_entity_association",
    "create_transaction",
    "verify_external_account",
    "complete_verification",
    "archive_resource",
    "read_resource",
    "list_resources",
    "TENACITY_WAIT_EXP_2_10",
    "TENACITY_WAIT_EXP_2_5",
    "TENACITY_STOP_30",
    "TENACITY_STOP_60",
]
