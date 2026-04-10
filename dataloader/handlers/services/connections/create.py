from __future__ import annotations

from loguru import logger
from modern_treasury._exceptions import APIStatusError
from modern_treasury.types.connection import Connection

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
    """Create a sandbox connection via ``POST /connections``."""
    logger.bind(ref=typed_ref).info("Creating sandbox connection")

    body: dict[str, str] = {"entity_id": resolved["entity_id"]}
    if resolved.get("nickname"):
        body["nickname"] = resolved["nickname"]

    try:
        result = await mt.sdk.post(
            "/api/connections",
            cast_to=Connection,
            body=body,
            options={"idempotency_key": idempotency_key},
        )
    except APIStatusError as exc:
        if exc.status_code == 405:
            raise RuntimeError(
                "Connection creation is sandbox-only. "
                "Production orgs must have connections provisioned by MT."
            ) from exc
        if exc.status_code == 422:
            logger.bind(ref=typed_ref).info("Connection already exists, looking up by entity_id")
            async for conn in mt.sdk.connections.list():
                if getattr(conn, "vendor_id", None) == resolved["entity_id"]:
                    return HandlerResult(
                        created_id=conn.id,
                        resource_type="connection",
                        deletable=DELETABILITY["connection"],
                    )
            raise RuntimeError(
                f"Connection with entity_id '{resolved['entity_id']}' returned "
                f"422 (duplicate) but could not be found via list. "
                f"Check your sandbox state."
            ) from exc
        raise

    return HandlerResult(
        created_id=result.id,
        resource_type="connection",
        deletable=DELETABILITY["connection"],
    )
