from __future__ import annotations

from loguru import logger

from dataloader.handlers.constants import EmitFn
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
    logger.bind(ref=typed_ref).info("Initiating external account verification")
    ea_id = resolved.pop("external_account_ref")
    result = await mt.sdk.external_accounts.verify(
        ea_id,
        originating_account_id=resolved["originating_account_id"],
        payment_type=resolved.get("payment_type", "rtp"),
        currency=resolved.get("currency"),
        priority=resolved.get("priority"),
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="verify_external_account",
        deletable=False,
    )
