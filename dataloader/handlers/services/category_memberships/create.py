from __future__ import annotations

from loguru import logger
from modern_treasury._exceptions import APIStatusError

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
    category_id = resolved["category_id"]
    ledger_account_id = resolved["ledger_account_id"]

    logger.bind(ref=typed_ref).info("Adding ledger account to category")

    try:
        await mt.sdk.ledger_account_categories.add_ledger_account(
            ledger_account_id,
            id=category_id,
            idempotency_key=idempotency_key,
        )
    except APIStatusError as exc:
        if exc.status_code == 422 and "already in" in str(exc).lower():
            logger.bind(ref=typed_ref).info(
                "Ledger account already in category — treating as success"
            )
        else:
            raise

    return HandlerResult(
        created_id=f"{category_id}:{ledger_account_id}",
        resource_type="category_membership",
        deletable=DELETABILITY["category_membership"],
    )
