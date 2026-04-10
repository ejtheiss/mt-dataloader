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
    logger.bind(ref=typed_ref).info("Creating virtual account")
    result = await mt.sdk.virtual_accounts.create(
        **resolved,
        idempotency_key=idempotency_key,
    )

    child_refs: dict[str, str] = {}
    if result.ledger_account_id:
        child_refs["ledger_account"] = result.ledger_account_id

    return HandlerResult(
        created_id=result.id,
        resource_type="virtual_account",
        child_refs=child_refs,
        deletable=DELETABILITY["virtual_account"],
    )
