from __future__ import annotations

from loguru import logger
from tenacity import RetryError

from dataloader.handlers.constants import DELETABILITY, EmitFn
from dataloader.handlers.contracts.incoming_payment_detail import (
    ResolvedIncomingPaymentDetailPayload,
)
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
    ResolvedIncomingPaymentDetailPayload.model_validate(resolved)
    logger.bind(ref=typed_ref).info("Simulating incoming payment detail")

    meta = resolved.pop("metadata", None)
    if meta:
        logger.bind(ref=typed_ref).info(
            "IPD metadata stripped (MT simulation endpoint does not accept it): {}",
            list(meta.keys()),
        )

    result = await mt.sdk.incoming_payment_details.create_async(
        **resolved,
        idempotency_key=idempotency_key,
    )

    try:
        ipd = await mt.poll_ipd_until_completed(result.id, typed_ref, emit_sse)
    except RetryError as e:
        last_result = e.last_attempt.result()
        raise RuntimeError(
            f"IPD '{result.id}' did not reach 'completed' status within 30s. "
            f"Last status: '{last_result.status}'"
        ) from e

    child_refs: dict[str, str] = {}
    if ipd.transaction_id:
        child_refs["transaction"] = ipd.transaction_id
    if ipd.ledger_transaction_id:
        child_refs["ledger_transaction"] = ipd.ledger_transaction_id

    if child_refs:
        logger.bind(ref=typed_ref, child_refs=child_refs).info(
            "IPD completed — registered auto-created child refs"
        )

    return HandlerResult(
        created_id=result.id,
        resource_type="incoming_payment_detail",
        child_refs=child_refs,
        deletable=DELETABILITY["incoming_payment_detail"],
    )
