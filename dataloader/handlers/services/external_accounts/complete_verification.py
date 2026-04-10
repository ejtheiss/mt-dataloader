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
    """Complete EA verification by reading micro-deposit PO amounts."""
    logger.bind(ref=typed_ref).info("Completing external account verification")
    ea_id = resolved.pop("external_account_ref")

    amounts: list[int] = []
    async for po in mt.sdk.payment_orders.list(
        per_page=10,
        metadata={"verification_external_account_id": ea_id},
    ):
        if po.amount and len(amounts) < 2:
            amounts.append(po.amount)

    if len(amounts) < 2:
        raise RuntimeError(
            f"Could not find 2 micro-deposit POs for EA '{ea_id}'. "
            f"Found {len(amounts)} amounts: {amounts}. "
            f"Ensure verify_external_account ran first."
        )

    result = await mt.sdk.external_accounts.complete_verification(
        ea_id,
        amounts=amounts,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="complete_verification",
        deletable=False,
    )
