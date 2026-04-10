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
    logger.bind(ref=typed_ref).info("Creating ledger account category")
    result = await mt.sdk.ledger_account_categories.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="ledger_account_category",
        deletable=DELETABILITY["ledger_account_category"],
    )
