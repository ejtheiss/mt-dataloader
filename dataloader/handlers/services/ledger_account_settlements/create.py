from __future__ import annotations

from loguru import logger
from tenacity import RetryError

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
    logger.bind(ref=typed_ref).info("Creating ledger account settlement")
    result = await mt.sdk.ledger_account_settlements.create(
        **resolved,
        idempotency_key=idempotency_key,
    )

    if result.status in ("pending", "processing"):
        try:
            result = await mt.poll_settlement_until_terminal(result.id, typed_ref, emit_sse)
        except RetryError:
            logger.bind(ref=typed_ref).warning(
                "Settlement did not reach terminal state within timeout — proceeding"
            )

    return HandlerResult(
        created_id=result.id,
        resource_type="ledger_account_settlement",
        deletable=DELETABILITY["ledger_account_settlement"],
    )
