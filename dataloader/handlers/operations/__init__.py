"""Public handler callables (``AsyncModernTreasury`` + ``emit_sse``) for re-exports."""

from __future__ import annotations

from modern_treasury import AsyncModernTreasury

from dataloader.handlers.constants import EmitFn
from dataloader.handlers.mt_client import MTClient
from dataloader.handlers.services.category_memberships.create import (
    call as _category_membership_create,
)
from dataloader.handlers.services.connections.create import call as _connection_create
from dataloader.handlers.services.counterparties.create import call as _counterparty_create
from dataloader.handlers.services.expected_payments.create import call as _expected_payment_create
from dataloader.handlers.services.external_accounts.complete_verification import (
    call as _complete_verification,
)
from dataloader.handlers.services.external_accounts.create import call as _external_account_create
from dataloader.handlers.services.external_accounts.verify import call as _verify_external_account
from dataloader.handlers.services.incoming_payment_details.create import (
    call as _incoming_payment_detail_create,
)
from dataloader.handlers.services.internal_accounts.create import call as _internal_account_create
from dataloader.handlers.services.ledger_account_balance_monitors.create import (
    call as _ledger_account_balance_monitor_create,
)
from dataloader.handlers.services.ledger_account_categories.create import (
    call as _ledger_account_category_create,
)
from dataloader.handlers.services.ledger_account_settlements.create import (
    call as _ledger_account_settlement_create,
)
from dataloader.handlers.services.ledger_account_statements.create import (
    call as _ledger_account_statement_create,
)
from dataloader.handlers.services.ledger_accounts.create import call as _ledger_account_create
from dataloader.handlers.services.ledger_transactions.create import (
    call as _ledger_transaction_create,
)
from dataloader.handlers.services.ledger_transactions.transition import (
    call as _transition_ledger_transaction,
)
from dataloader.handlers.services.ledgers.create import call as _ledger_create
from dataloader.handlers.services.legal_entities.create import call as _legal_entity_create
from dataloader.handlers.services.legal_entity_associations.create import (
    call as _legal_entity_association_create,
)
from dataloader.handlers.services.nested_categories.create import call as _nested_category_create
from dataloader.handlers.services.payment_orders.create import call as _payment_order_create
from dataloader.handlers.services.queries.list_resources import call as _list_resources
from dataloader.handlers.services.queries.read_resource import call as _read_resource
from dataloader.handlers.services.returns.create import call as _return_create
from dataloader.handlers.services.reversals.create import call as _reversal_create
from dataloader.handlers.services.system.archive_resource import call as _archive_resource
from dataloader.handlers.services.transactions.create import call as _transaction_create
from dataloader.handlers.services.virtual_accounts.create import call as _virtual_account_create


def _mt(client: AsyncModernTreasury) -> MTClient:
    return MTClient(client)


async def archive_resource(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _archive_resource(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def complete_verification(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _complete_verification(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_category_membership(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _category_membership_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_connection(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _connection_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_counterparty(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _counterparty_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_expected_payment(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _expected_payment_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_external_account(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _external_account_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_incoming_payment_detail(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _incoming_payment_detail_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_internal_account(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _internal_account_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_ledger(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _ledger_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_ledger_account(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _ledger_account_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_ledger_account_balance_monitor(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _ledger_account_balance_monitor_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_ledger_account_category(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _ledger_account_category_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_ledger_account_settlement(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _ledger_account_settlement_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_ledger_account_statement(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _ledger_account_statement_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_ledger_transaction(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _ledger_transaction_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_legal_entity(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _legal_entity_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_legal_entity_association(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _legal_entity_association_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_nested_category(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _nested_category_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_payment_order(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _payment_order_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_return(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _return_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_reversal(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _reversal_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_transaction(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _transaction_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def create_virtual_account(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _virtual_account_create(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def list_resources(
    client: AsyncModernTreasury, resource_type: str, *, limit: int = 100, **filters
):
    return await _list_resources(_mt(client), resource_type, limit=limit, **filters)


async def read_resource(client: AsyncModernTreasury, resource_type: str, resource_id: str):
    return await _read_resource(_mt(client), resource_type, resource_id)


async def transition_ledger_transaction(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _transition_ledger_transaction(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


async def verify_external_account(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
):
    return await _verify_external_account(
        _mt(client), emit_sse, resolved, idempotency_key=idempotency_key, typed_ref=typed_ref
    )


__all__ = [
    "archive_resource",
    "complete_verification",
    "create_category_membership",
    "create_connection",
    "create_counterparty",
    "create_expected_payment",
    "create_external_account",
    "create_incoming_payment_detail",
    "create_internal_account",
    "create_ledger",
    "create_ledger_account",
    "create_ledger_account_balance_monitor",
    "create_ledger_account_category",
    "create_ledger_account_settlement",
    "create_ledger_account_statement",
    "create_ledger_transaction",
    "create_legal_entity",
    "create_legal_entity_association",
    "create_nested_category",
    "create_payment_order",
    "create_return",
    "create_reversal",
    "create_transaction",
    "create_virtual_account",
    "list_resources",
    "read_resource",
    "transition_ledger_transaction",
    "verify_external_account",
]
