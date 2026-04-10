from __future__ import annotations

from loguru import logger

from dataloader.handlers.constants import SDK_ATTR_MAP, EmitFn
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
    resource_type = resolved.pop("resource_type")
    resource_ref = resolved.pop("resource_ref")
    method = resolved.pop("archive_method", "delete")

    logger.bind(ref=typed_ref, target_type=resource_type, method=method).info("Archiving resource")

    if method == "delete":
        sdk_attr = SDK_ATTR_MAP.get(resource_type)
        if sdk_attr:
            await getattr(mt.sdk, sdk_attr).delete(resource_ref)
    elif method == "archive":
        await mt.sdk.ledger_transactions.update(resource_ref, status="archived")
    elif method == "request_closure":
        logger.bind(ref=typed_ref).info(
            "Requesting IA closure — this is a request, not an immediate close"
        )
        await mt.sdk.internal_accounts.request_closure(resource_ref)

    return HandlerResult(
        created_id=resource_ref,
        resource_type="archive_resource",
        deletable=False,
    )
