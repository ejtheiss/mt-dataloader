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
    logger.bind(ref=typed_ref).info("Creating legal entity association")
    result = await mt.sdk.legal_entity_associations.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="legal_entity_association",
        deletable=DELETABILITY["legal_entity_association"],
    )
