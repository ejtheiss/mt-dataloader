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
    logger.bind(ref=typed_ref).info("Creating counterparty")
    result = await mt.sdk.counterparties.create(
        **resolved,
        idempotency_key=idempotency_key,
    )

    child_refs: dict[str, str] = {}
    if result.accounts:
        for i, account in enumerate(result.accounts):
            if account.id:
                child_refs[f"account[{i}]"] = account.id
        logger.bind(
            ref=typed_ref,
            account_count=len(result.accounts),
            child_refs=child_refs,
        ).info("Counterparty created with inline accounts")
    else:
        logger.bind(ref=typed_ref).warning(
            "Counterparty created but API returned no accounts — "
            "child refs (e.g. account[0]) will be unresolvable"
        )

    return HandlerResult(
        created_id=result.id,
        resource_type="counterparty",
        child_refs=child_refs,
        deletable=DELETABILITY["counterparty"],
    )
