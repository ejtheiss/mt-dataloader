from __future__ import annotations

from loguru import logger

from dataloader.handlers.constants import DELETABILITY, EmitFn
from dataloader.handlers.mt_client import MTClient
from models import HandlerResult


async def call(
    mt: MTClient,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    """Update an existing LT's status (e.g., pending -> posted)."""
    logger.bind(ref=typed_ref).info("Transitioning ledger transaction")
    lt_id = resolved.pop("ledger_transaction_id")
    new_status = resolved.pop("status")

    result = await mt.sdk.ledger_transactions.update(
        lt_id,
        status=new_status,
        idempotency_key=idempotency_key,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="transition_ledger_transaction",
        deletable=DELETABILITY["transition_ledger_transaction"],
    )
