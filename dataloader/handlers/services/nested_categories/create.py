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
    parent_id = resolved["parent_category_id"]
    sub_id = resolved["sub_category_id"]

    logger.bind(ref=typed_ref).info("Adding nested sub-category")

    try:
        await mt.sdk.ledger_account_categories.add_nested_category(
            sub_id,
            id=parent_id,
            idempotency_key=idempotency_key,
        )
    except APIStatusError as exc:
        if exc.status_code == 422 and "already" in str(exc).lower():
            logger.bind(ref=typed_ref).info("Sub-category already nested — treating as success")
        else:
            raise

    return HandlerResult(
        created_id=f"{parent_id}:{sub_id}",
        resource_type="nested_category",
        deletable=DELETABILITY["nested_category"],
    )
