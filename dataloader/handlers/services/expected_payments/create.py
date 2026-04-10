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
    logger.bind(ref=typed_ref).info("Creating expected payment")
    meta = resolved.get("metadata", {})
    if meta:
        resolved["metadata"] = {k: v for k, v in meta.items() if not k.startswith("_flow_")}
    result = await mt.sdk.expected_payments.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="expected_payment",
        deletable=DELETABILITY["expected_payment"],
    )
