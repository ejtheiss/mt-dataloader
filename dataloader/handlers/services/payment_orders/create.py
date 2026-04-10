from __future__ import annotations

from loguru import logger
from modern_treasury._exceptions import APIStatusError

from dataloader.handlers.constants import DELETABILITY, EmitFn
from dataloader.handlers.contracts.payment_order import ResolvedPaymentOrderPayload
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
    ResolvedPaymentOrderPayload.model_validate(resolved)
    logger.bind(
        ref=typed_ref,
        direction=resolved.get("direction"),
        po_type=resolved.get("type"),
        has_receiving=("receiving_account_id" in resolved or "receiving_account" in resolved),
        has_ledger_txn="ledger_transaction" in resolved,
        payload_keys=sorted(resolved.keys()),
    ).info("Creating payment order")
    logger.bind(ref=typed_ref, resolved_payload=resolved).debug("Full PO payload")

    meta = resolved.get("metadata", {})
    if meta:
        resolved["metadata"] = {k: v for k, v in meta.items() if not k.startswith("_flow_")}

    try:
        result = await mt.sdk.payment_orders.create(
            **resolved,
            idempotency_key=idempotency_key,
        )
    except APIStatusError as exc:
        logger.bind(
            ref=typed_ref,
            status=exc.status_code,
            body=exc.body,
            resolved_payload=resolved,
        ).error("Payment order API error")
        raise

    child_refs: dict[str, str] = {}
    if result.ledger_transaction_id:
        child_refs["ledger_transaction"] = result.ledger_transaction_id

    return HandlerResult(
        created_id=result.id,
        resource_type="payment_order",
        child_refs=child_refs,
        deletable=DELETABILITY["payment_order"],
    )
