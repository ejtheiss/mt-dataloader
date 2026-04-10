from __future__ import annotations

from loguru import logger
from tenacity import RetryError

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
    logger.bind(ref=typed_ref).info("Creating legal entity")
    result = await mt.sdk.legal_entities.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    if result.status != "active":
        try:
            result = await mt.poll_legal_entity_until_settled(
                result.id,
                typed_ref,
                emit_sse,
            )
        except RetryError as e:
            last_result = e.last_attempt.result()
            status = getattr(last_result, "status", "unknown")
            if status == "denied":
                raise RuntimeError(f"Legal entity {typed_ref} was denied by compliance") from e
            logger.bind(ref=typed_ref, status=status).warning(
                "LE did not reach 'active' within timeout — proceeding anyway"
            )
    return HandlerResult(
        created_id=result.id,
        resource_type="legal_entity",
        deletable=DELETABILITY["legal_entity"],
    )
