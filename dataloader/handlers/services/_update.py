"""Generic SDK ``.update()`` handler for reconcile / apply-update flows."""

from __future__ import annotations

from loguru import logger

from dataloader.handlers.constants import DELETABILITY, EmitFn
from dataloader.handlers.mt_client import MTClient
from models import HandlerResult

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


async def generic_update(
    mt: MTClient,
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
    sdk_resource = getattr(mt.sdk, sdk_attr)
    result = await sdk_resource.update(resource_id, **resolved)
    return HandlerResult(
        created_id=result.id,
        resource_type=resource_type,
        deletable=DELETABILITY.get(resource_type, False),
    )
