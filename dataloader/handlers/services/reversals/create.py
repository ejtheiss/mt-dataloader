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
    logger.bind(ref=typed_ref).info("Creating payment order reversal")
    payment_order_id = resolved.pop("payment_order_id")

    try:
        po = await mt.poll_po_until_reversible(payment_order_id, typed_ref, emit_sse)
        logger.bind(ref=typed_ref, po_status=po.status).info("PO reached reversible state")
    except RetryError as e:
        last = e.last_attempt.result()
        raise RuntimeError(
            f"PO '{payment_order_id}' did not reach a reversible state within 60s. "
            f"Last status: '{last.status}'"
        ) from e

    result = await mt.sdk.payment_orders.reversals.create(
        payment_order_id,
        **resolved,
        idempotency_key=idempotency_key,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="reversal",
        deletable=DELETABILITY["reversal"],
    )
