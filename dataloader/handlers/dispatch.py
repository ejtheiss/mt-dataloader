from __future__ import annotations

import functools

from modern_treasury import AsyncModernTreasury

from .constants import EmitFn, HandlerFn, UpdateHandlerFn
from .mt_client import MTClient
from .services._update import generic_update
from .services.category_memberships.create import call as category_membership_create
from .services.connections.create import call as connection_create
from .services.counterparties.create import call as counterparty_create
from .services.expected_payments.create import call as expected_payment_create
from .services.external_accounts.complete_verification import call as complete_verification_call
from .services.external_accounts.create import call as external_account_create
from .services.external_accounts.verify import call as verify_external_account_call
from .services.incoming_payment_details.create import call as incoming_payment_detail_create
from .services.internal_accounts.create import call as internal_account_create
from .services.ledger_account_balance_monitors.create import (
    call as ledger_account_balance_monitor_create,
)
from .services.ledger_account_categories.create import call as ledger_account_category_create
from .services.ledger_account_settlements.create import call as ledger_account_settlement_create
from .services.ledger_account_statements.create import call as ledger_account_statement_create
from .services.ledger_accounts.create import call as ledger_account_create
from .services.ledger_transactions.create import call as ledger_transaction_create
from .services.ledger_transactions.transition import call as transition_ledger_transaction_call
from .services.ledgers.create import call as ledger_create
from .services.legal_entities.create import call as legal_entity_create
from .services.legal_entity_associations.create import call as legal_entity_association_create
from .services.nested_categories.create import call as nested_category_create
from .services.payment_orders.create import call as payment_order_create
from .services.returns.create import call as return_create
from .services.reversals.create import call as reversal_create
from .services.system.archive_resource import call as archive_resource_call
from .services.transactions.create import call as transaction_create
from .services.virtual_accounts.create import call as virtual_account_create


def build_update_dispatch(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
) -> dict[str, UpdateHandlerFn]:
    """Build the update handler dispatch table."""
    bind = functools.partial
    mt = MTClient(client)

    def _bind(resource_type: str, sdk_attr: str) -> UpdateHandlerFn:
        return bind(
            generic_update,
            mt,
            emit_sse,
            resource_type=resource_type,
            sdk_attr=sdk_attr,
        )

    return {
        "internal_account": _bind("internal_account", "internal_accounts"),
        "legal_entity": _bind("legal_entity", "legal_entities"),
        "counterparty": _bind("counterparty", "counterparties"),
        "ledger": _bind("ledger", "ledgers"),
        "ledger_account": _bind("ledger_account", "ledger_accounts"),
        "ledger_account_category": _bind(
            "ledger_account_category",
            "ledger_account_categories",
        ),
        "external_account": _bind("external_account", "external_accounts"),
        "virtual_account": _bind("virtual_account", "virtual_accounts"),
        "expected_payment": _bind("expected_payment", "expected_payments"),
    }


def build_handler_dispatch(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
) -> dict[str, HandlerFn]:
    """Build the handler dispatch table with ``MTClient`` and ``emit_sse`` pre-bound."""
    bind = functools.partial
    mt = MTClient(client)

    return {
        "connection": bind(connection_create, mt, emit_sse),
        "legal_entity": bind(legal_entity_create, mt, emit_sse),
        "legal_entity_association": bind(legal_entity_association_create, mt, emit_sse),
        "ledger": bind(ledger_create, mt, emit_sse),
        "counterparty": bind(counterparty_create, mt, emit_sse),
        "ledger_account": bind(ledger_account_create, mt, emit_sse),
        "internal_account": bind(internal_account_create, mt, emit_sse),
        "external_account": bind(external_account_create, mt, emit_sse),
        "ledger_account_category": bind(ledger_account_category_create, mt, emit_sse),
        "ledger_account_settlement": bind(ledger_account_settlement_create, mt, emit_sse),
        "ledger_account_balance_monitor": bind(
            ledger_account_balance_monitor_create,
            mt,
            emit_sse,
        ),
        "ledger_account_statement": bind(ledger_account_statement_create, mt, emit_sse),
        "virtual_account": bind(virtual_account_create, mt, emit_sse),
        "expected_payment": bind(expected_payment_create, mt, emit_sse),
        "payment_order": bind(payment_order_create, mt, emit_sse),
        "incoming_payment_detail": bind(incoming_payment_detail_create, mt, emit_sse),
        "ledger_transaction": bind(ledger_transaction_create, mt, emit_sse),
        "transaction": bind(transaction_create, mt, emit_sse),
        "return": bind(return_create, mt, emit_sse),
        "reversal": bind(reversal_create, mt, emit_sse),
        "category_membership": bind(category_membership_create, mt, emit_sse),
        "nested_category": bind(nested_category_create, mt, emit_sse),
        "transition_ledger_transaction": bind(transition_ledger_transaction_call, mt, emit_sse),
        "verify_external_account": bind(verify_external_account_call, mt, emit_sse),
        "complete_verification": bind(complete_verification_call, mt, emit_sse),
        "archive_resource": bind(archive_resource_call, mt, emit_sse),
    }
