from __future__ import annotations

import functools

from loguru import logger
from modern_treasury import AsyncModernTreasury

from models import HandlerResult

from .constants import DELETABILITY, EmitFn, HandlerFn, UpdateHandlerFn
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
    transition_ledger_transaction,
    verify_external_account,
)

_STRIP_ON_UPDATE: dict[str, set[str]] = {
    "internal_account": {"connection_id", "currency"},
    "legal_entity": {"legal_entity_type", "connection_id"},
    "counterparty": set(),
    "ledger": set(),
    "ledger_account": {"currency", "ledger_id", "normal_balance"},
    "ledger_account_category": {"currency", "ledger_id", "normal_balance"},
    "external_account": {"counterparty_id"},
    "virtual_account": {"internal_account_id"},
    "expected_payment": set(),
}


async def _generic_update(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    resource_id: str,
    resource_type: str,
    sdk_attr: str,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    for key in _STRIP_ON_UPDATE.get(resource_type, set()):
        resolved.pop(key, None)
    logger.bind(ref=typed_ref).info("Updating {} {}", resource_type, resource_id[:12])
    sdk_resource = getattr(client, sdk_attr)
    result = await sdk_resource.update(resource_id, **resolved)
    return HandlerResult(
        created_id=result.id,
        resource_type=resource_type,
        deletable=DELETABILITY.get(resource_type, False),
    )


def build_update_dispatch(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
) -> dict[str, UpdateHandlerFn]:
    """Build the update handler dispatch table.

    Each handler calls ``.update(resource_id, ...)`` instead of ``.create()``,
    stripping immutable fields per resource type.
    """
    bind = functools.partial

    def _bind(resource_type: str, sdk_attr: str) -> UpdateHandlerFn:
        return bind(
            _generic_update,
            client,
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
    """Build the handler dispatch table with client and emit_sse pre-bound.

    The engine calls each handler as:
        result = await handler(resolved, idempotency_key=..., typed_ref=...)
    """
    bind = functools.partial

    return {
        "connection": bind(create_connection, client, emit_sse),
        "legal_entity": bind(create_legal_entity, client, emit_sse),
        "legal_entity_association": bind(create_legal_entity_association, client, emit_sse),
        "ledger": bind(create_ledger, client, emit_sse),
        "counterparty": bind(create_counterparty, client, emit_sse),
        "ledger_account": bind(create_ledger_account, client, emit_sse),
        "internal_account": bind(create_internal_account, client, emit_sse),
        "external_account": bind(create_external_account, client, emit_sse),
        "ledger_account_category": bind(create_ledger_account_category, client, emit_sse),
        "ledger_account_settlement": bind(create_ledger_account_settlement, client, emit_sse),
        "ledger_account_balance_monitor": bind(
            create_ledger_account_balance_monitor, client, emit_sse
        ),
        "ledger_account_statement": bind(create_ledger_account_statement, client, emit_sse),
        "virtual_account": bind(create_virtual_account, client, emit_sse),
        "expected_payment": bind(create_expected_payment, client, emit_sse),
        "payment_order": bind(create_payment_order, client, emit_sse),
        "incoming_payment_detail": bind(create_incoming_payment_detail, client, emit_sse),
        "ledger_transaction": bind(create_ledger_transaction, client, emit_sse),
        "transaction": bind(create_transaction, client, emit_sse),
        "return": bind(create_return, client, emit_sse),
        "reversal": bind(create_reversal, client, emit_sse),
        "category_membership": bind(create_category_membership, client, emit_sse),
        "nested_category": bind(create_nested_category, client, emit_sse),
        "transition_ledger_transaction": bind(transition_ledger_transaction, client, emit_sse),
        "verify_external_account": bind(verify_external_account, client, emit_sse),
        "complete_verification": bind(complete_verification, client, emit_sse),
        "archive_resource": bind(archive_resource, client, emit_sse),
    }
