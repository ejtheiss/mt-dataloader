from __future__ import annotations

from typing import Any

from loguru import logger
from modern_treasury._exceptions import APIStatusError
from tenacity import retry, retry_if_exception

from dataloader.handlers.constants import (
    DELETABILITY,
    TENACITY_STOP_30,
    TENACITY_WAIT_EXP_2_5,
    EmitFn,
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
    logger.bind(ref=typed_ref).info("Creating return")
    returnable_id = resolved.pop("returnable_id")
    returnable_type = resolved.pop("returnable_type", "incoming_payment_detail")
    resolved.pop("metadata", None)  # MT's returns.create() has no metadata param

    wait_detail = (
        "PO may still be settling"
        if returnable_type == "payment_order"
        else "IPD may still be settling"
    )

    async def _before_sleep(retry_state: Any) -> None:
        await emit_sse(
            "waiting",
            typed_ref,
            {"attempt": retry_state.attempt_number, "detail": wait_detail},
        )

    @retry(
        wait=TENACITY_WAIT_EXP_2_5,
        stop=TENACITY_STOP_30,
        retry=retry_if_exception(
            lambda exc: (
                isinstance(exc, APIStatusError) and exc.status_code in (429, 500, 502, 503, 504)
            )
        ),
        before_sleep=_before_sleep,
    )
    async def _create_with_retry() -> Any:
        return await mt.sdk.returns.create(
            returnable_id=returnable_id,
            returnable_type=returnable_type,
            **resolved,
            idempotency_key=idempotency_key,
        )

    result = await _create_with_retry()
    return HandlerResult(
        created_id=result.id,
        resource_type="return",
        deletable=DELETABILITY["return"],
    )
