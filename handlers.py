"""Async handler functions for Modern Treasury SDK resource creation.

Each handler:
1. Receives a resolved dict (all $ref: strings replaced with UUIDs)
2. Calls the corresponding AsyncModernTreasury SDK method
3. Returns a HandlerResult with created ID, child refs, and deletability

This is the ONLY module that imports the MT SDK.
"""

from __future__ import annotations

import functools
from typing import Any, Awaitable, Callable

from loguru import logger
from modern_treasury import AsyncModernTreasury
from modern_treasury._exceptions import APIStatusError
from modern_treasury.types.connection import Connection
from tenacity import (
    RetryError,
    retry,
    retry_if_exception,
    retry_if_result,
    stop_after_delay,
    wait_exponential,
)

from models import HandlerResult

__all__ = [
    "DELETABILITY",
    "SDK_ATTR_MAP",
    "build_handler_dispatch",
    "build_update_dispatch",
    "create_connection",
    "create_legal_entity",
    "create_ledger",
    "create_counterparty",
    "create_ledger_account",
    "create_internal_account",
    "create_external_account",
    "create_ledger_account_category",
    "create_virtual_account",
    "create_expected_payment",
    "create_payment_order",
    "create_incoming_payment_detail",
    "create_ledger_transaction",
    "create_return",
    "create_reversal",
    "create_category_membership",
    "create_nested_category",
    "transition_ledger_transaction",
    "create_ledger_account_settlement",
    "create_ledger_account_balance_monitor",
    "create_ledger_account_statement",
    "create_legal_entity_association",
    "create_transaction",
    "verify_external_account",
    "complete_verification",
    "archive_resource",
    "read_resource",
    "list_resources",
]

# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

EmitFn = Callable[[str, str, dict[str, Any]], Awaitable[None]]
HandlerFn = Callable[..., Awaitable[HandlerResult]]
UpdateHandlerFn = Callable[..., Awaitable[HandlerResult]]

# ---------------------------------------------------------------------------
# Deletability mapping (resource_type -> can be deleted via API)
# ---------------------------------------------------------------------------

DELETABILITY: dict[str, bool] = {
    "connection": False,
    "legal_entity": False,
    "legal_entity_association": False,
    "ledger": True,
    "counterparty": True,
    "ledger_account": True,
    "internal_account": False,
    "external_account": True,
    "ledger_account_category": True,
    "ledger_account_settlement": False,
    "ledger_account_balance_monitor": True,
    "ledger_account_statement": False,
    "virtual_account": True,
    "expected_payment": True,
    "payment_order": False,
    "incoming_payment_detail": False,
    "ledger_transaction": False,
    "transaction": False,
    "return": False,
    "reversal": False,
    "category_membership": True,
    "nested_category": True,
    "transition_ledger_transaction": False,
    "verify_external_account": False,
    "complete_verification": False,
    "archive_resource": False,
}

SDK_ATTR_MAP: dict[str, str] = {
    "connection": "connections",
    "legal_entity": "legal_entities",
    "ledger": "ledgers",
    "counterparty": "counterparties",
    "ledger_account": "ledger_accounts",
    "internal_account": "internal_accounts",
    "external_account": "external_accounts",
    "ledger_account_category": "ledger_account_categories",
    "virtual_account": "virtual_accounts",
    "expected_payment": "expected_payments",
    "payment_order": "payment_orders",
    "incoming_payment_detail": "incoming_payment_details",
    "ledger_transaction": "ledger_transactions",
    "return": "returns",
    "reversal": "payment_orders",
    "ledger_account_settlement": "ledger_account_settlements",
    "ledger_account_balance_monitor": "ledger_account_balance_monitors",
    "ledger_account_statement": "ledger_account_statements",
    "transaction": "transactions",
}

# ---------------------------------------------------------------------------
# Lifecycle polling helpers
# ---------------------------------------------------------------------------


async def _poll_ipd_status(
    client: AsyncModernTreasury,
    ipd_id: str,
    typed_ref: str,
    emit_sse: EmitFn,
) -> Any:
    """Poll an IPD until status == 'completed'.

    Uses tenacity with 2-10s exponential backoff, 30s timeout.
    Emits SSE 'waiting' events via before_sleep callback.
    Returns the completed IncomingPaymentDetail response object.
    """

    async def _before_sleep(retry_state: Any) -> None:
        await emit_sse(
            "waiting",
            typed_ref,
            {"attempt": retry_state.attempt_number},
        )

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_delay(30),
        retry=retry_if_result(lambda r: r.status != "completed"),
        before_sleep=_before_sleep,
    )
    async def _poll() -> Any:
        return await client.incoming_payment_details.retrieve(ipd_id)

    return await _poll()


_PO_REVERSIBLE_STATUSES = frozenset({"approved", "sent", "completed"})


async def _poll_po_status(
    client: AsyncModernTreasury,
    po_id: str,
    typed_ref: str,
    emit_sse: EmitFn,
) -> Any:
    """Poll a Payment Order until it reaches a reversible state.

    Sandbox POs advance through pending → approved → sent → completed
    asynchronously. Reversals require the PO to be past ``pending``.
    Uses tenacity with 2-10s exponential backoff, 60s timeout.
    """

    async def _before_sleep(retry_state: Any) -> None:
        last = retry_state.outcome.result() if retry_state.outcome else None
        status = getattr(last, "status", "unknown")
        await emit_sse(
            "waiting",
            typed_ref,
            {"attempt": retry_state.attempt_number, "detail": f"PO status: {status}"},
        )

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_delay(60),
        retry=retry_if_result(lambda r: r.status not in _PO_REVERSIBLE_STATUSES),
        before_sleep=_before_sleep,
    )
    async def _poll() -> Any:
        return await client.payment_orders.retrieve(po_id)

    return await _poll()


# ---------------------------------------------------------------------------
# Sandbox connection handler (client.post workaround)
# ---------------------------------------------------------------------------


async def create_connection(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    """Create a sandbox connection via ``POST /connections``.

    The SDK has no ``connections.create()`` — uses the generic ``client.post()``
    method.  Body is manually constructed (only ``entity_id`` + ``nickname``
    are accepted by the endpoint).  Idempotency key goes through ``options``.
    """
    logger.bind(ref=typed_ref).info("Creating sandbox connection")

    body: dict[str, str] = {"entity_id": resolved["entity_id"]}
    if resolved.get("nickname"):
        body["nickname"] = resolved["nickname"]

    try:
        result = await client.post(
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
            logger.bind(ref=typed_ref).info(
                "Connection already exists, looking up by entity_id"
            )
            async for conn in client.connections.list():
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


# ---------------------------------------------------------------------------
# Simple CRUD handlers
# ---------------------------------------------------------------------------


async def _poll_le_status(
    client: AsyncModernTreasury,
    le_id: str,
    typed_ref: str,
    emit_sse: EmitFn,
) -> Any:
    """Poll a Legal Entity until status == 'active'.

    The MT sandbox may keep the LE in 'pending' while compliance checks
    run asynchronously.  Internal accounts linked via ``legal_entity_id``
    will fail if the LE is not yet active.
    Uses tenacity with 2-10s exponential backoff, 60s timeout.
    """

    async def _before_sleep(retry_state: Any) -> None:
        last = retry_state.outcome.result() if retry_state.outcome else None
        status = getattr(last, "status", "unknown")
        await emit_sse(
            "waiting",
            typed_ref,
            {"attempt": retry_state.attempt_number, "detail": f"LE status: {status}"},
        )

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_delay(60),
        retry=retry_if_result(lambda r: r.status not in ("active", "denied")),
        before_sleep=_before_sleep,
    )
    async def _poll() -> Any:
        return await client.legal_entities.retrieve(le_id)

    return await _poll()


async def create_legal_entity(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Creating legal entity")
    result = await client.legal_entities.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    if result.status != "active":
        try:
            result = await _poll_le_status(
                client, result.id, typed_ref, emit_sse,
            )
        except RetryError as e:
            last_result = e.last_attempt.result()
            status = getattr(last_result, "status", "unknown")
            if status == "denied":
                raise RuntimeError(
                    f"Legal entity {typed_ref} was denied by compliance"
                ) from e
            logger.bind(ref=typed_ref, status=status).warning(
                "LE did not reach 'active' within timeout — proceeding anyway"
            )
    return HandlerResult(
        created_id=result.id,
        resource_type="legal_entity",
        deletable=DELETABILITY["legal_entity"],
    )


async def create_ledger(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Creating ledger")
    result = await client.ledgers.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="ledger",
        deletable=DELETABILITY["ledger"],
    )


async def create_ledger_account(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Creating ledger account")
    result = await client.ledger_accounts.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="ledger_account",
        deletable=DELETABILITY["ledger_account"],
    )


async def create_ledger_account_category(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Creating ledger account category")
    result = await client.ledger_account_categories.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="ledger_account_category",
        deletable=DELETABILITY["ledger_account_category"],
    )


# ---------------------------------------------------------------------------
# Handlers with child ref extraction
# ---------------------------------------------------------------------------


async def create_counterparty(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Creating counterparty")
    result = await client.counterparties.create(
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


async def create_internal_account(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Creating internal account")
    result = await client.internal_accounts.create(
        **resolved,
        idempotency_key=idempotency_key,
    )

    child_refs: dict[str, str] = {}
    if result.ledger_account_id:
        child_refs["ledger_account"] = result.ledger_account_id

    return HandlerResult(
        created_id=result.id,
        resource_type="internal_account",
        child_refs=child_refs,
        deletable=DELETABILITY["internal_account"],
    )


async def create_external_account(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Creating external account")
    result = await client.external_accounts.create(
        **resolved,
        idempotency_key=idempotency_key,
    )

    child_refs: dict[str, str] = {}
    if result.ledger_account_id:
        child_refs["ledger_account"] = result.ledger_account_id

    return HandlerResult(
        created_id=result.id,
        resource_type="external_account",
        child_refs=child_refs,
        deletable=DELETABILITY["external_account"],
    )


async def create_virtual_account(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Creating virtual account")
    result = await client.virtual_accounts.create(
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


# ---------------------------------------------------------------------------
# Business object handlers
# ---------------------------------------------------------------------------


async def create_expected_payment(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Creating expected payment")
    meta = resolved.get("metadata", {})
    if meta:
        resolved["metadata"] = {k: v for k, v in meta.items() if not k.startswith("_flow_")}
    result = await client.expected_payments.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="expected_payment",
        deletable=DELETABILITY["expected_payment"],
    )


async def create_payment_order(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
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
        result = await client.payment_orders.create(
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


# ---------------------------------------------------------------------------
# Lifecycle handlers (tenacity polling)
# ---------------------------------------------------------------------------


async def create_incoming_payment_detail(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Simulating incoming payment detail")

    meta = resolved.pop("metadata", None)
    if meta:
        logger.bind(ref=typed_ref).info(
            "IPD metadata stripped (MT simulation endpoint does not accept it): {}",
            list(meta.keys()),
        )

    result = await client.incoming_payment_details.create_async(
        **resolved,
        idempotency_key=idempotency_key,
    )

    try:
        ipd = await _poll_ipd_status(client, result.id, typed_ref, emit_sse)
    except RetryError as e:
        last_result = e.last_attempt.result()
        raise RuntimeError(
            f"IPD '{result.id}' did not reach 'completed' status within 30s. "
            f"Last status: '{last_result.status}'"
        ) from e

    child_refs: dict[str, str] = {}
    if ipd.transaction_id:
        child_refs["transaction"] = ipd.transaction_id
    if ipd.ledger_transaction_id:
        child_refs["ledger_transaction"] = ipd.ledger_transaction_id

    if child_refs:
        logger.bind(ref=typed_ref, child_refs=child_refs).info(
            "IPD completed — registered auto-created child refs"
        )

    return HandlerResult(
        created_id=result.id,
        resource_type="incoming_payment_detail",
        child_refs=child_refs,
        deletable=DELETABILITY["incoming_payment_detail"],
    )


async def create_ledger_transaction(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Creating ledger transaction")
    meta = resolved.get("metadata", {})
    if meta:
        resolved["metadata"] = {k: v for k, v in meta.items() if not k.startswith("_flow_")}
    result = await client.ledger_transactions.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="ledger_transaction",
        deletable=DELETABILITY["ledger_transaction"],
    )


async def create_return(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Creating return")
    returnable_id = resolved.pop("returnable_id")
    returnable_type = resolved.pop("returnable_type", "incoming_payment_detail")
    resolved.pop("metadata", None)  # MT's returns.create() has no metadata param

    wait_detail = (
        "PO may still be settling"
        if returnable_type == "payment_order"
        else "IPD may still be settling"
    )

    async def _before_sleep(retry_state: Any) -> None:
        await emit_sse(
            "waiting",
            typed_ref,
            {"attempt": retry_state.attempt_number, "detail": wait_detail},
        )

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=5),
        stop=stop_after_delay(30),
        retry=retry_if_exception(
            lambda exc: isinstance(exc, APIStatusError) and exc.status_code in (429, 500, 502, 503, 504)
        ),
        before_sleep=_before_sleep,
    )
    async def _create_with_retry() -> Any:
        return await client.returns.create(
            returnable_id=returnable_id,
            returnable_type=returnable_type,
            **resolved,
            idempotency_key=idempotency_key,
        )

    result = await _create_with_retry()
    return HandlerResult(
        created_id=result.id,
        resource_type="return",
        deletable=DELETABILITY["return"],
    )


async def create_reversal(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Creating payment order reversal")
    payment_order_id = resolved.pop("payment_order_id")

    try:
        po = await _poll_po_status(client, payment_order_id, typed_ref, emit_sse)
        logger.bind(ref=typed_ref, po_status=po.status).info(
            "PO reached reversible state"
        )
    except RetryError as e:
        last = e.last_attempt.result()
        raise RuntimeError(
            f"PO '{payment_order_id}' did not reach a reversible state within 60s. "
            f"Last status: '{last.status}'"
        ) from e

    result = await client.payment_orders.reversals.create(
        payment_order_id,
        **resolved,
        idempotency_key=idempotency_key,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="reversal",
        deletable=DELETABILITY["reversal"],
    )


# ---------------------------------------------------------------------------
# Post-create mutation handlers
# ---------------------------------------------------------------------------


async def create_category_membership(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    category_id = resolved["category_id"]
    ledger_account_id = resolved["ledger_account_id"]

    logger.bind(ref=typed_ref).info(
        "Adding ledger account to category"
    )

    try:
        await client.ledger_account_categories.add_ledger_account(
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


async def create_nested_category(
    client: AsyncModernTreasury,
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
        await client.ledger_account_categories.add_nested_category(
            sub_id,
            id=parent_id,
            idempotency_key=idempotency_key,
        )
    except APIStatusError as exc:
        if exc.status_code == 422 and "already" in str(exc).lower():
            logger.bind(ref=typed_ref).info(
                "Sub-category already nested — treating as success"
            )
        else:
            raise

    return HandlerResult(
        created_id=f"{parent_id}:{sub_id}",
        resource_type="nested_category",
        deletable=DELETABILITY["nested_category"],
    )


# ---------------------------------------------------------------------------
# Lifecycle transition handlers
# ---------------------------------------------------------------------------


async def transition_ledger_transaction(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    """Update an existing LT's status (e.g., pending -> posted)."""
    logger.bind(ref=typed_ref).info("Transitioning ledger transaction")
    lt_id = resolved.pop("ledger_transaction_id")
    new_status = resolved.pop("status")

    result = await client.ledger_transactions.update(
        lt_id,
        status=new_status,
        idempotency_key=idempotency_key,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="transition_ledger_transaction",
        deletable=DELETABILITY["transition_ledger_transaction"],
    )


# ---------------------------------------------------------------------------
# New resource handlers (Feature Audit)
# ---------------------------------------------------------------------------


async def _poll_settlement_status(
    client: AsyncModernTreasury,
    settlement_id: str,
    typed_ref: str,
    emit_sse: EmitFn,
) -> Any:
    """Poll a settlement until it reaches a terminal state."""

    async def _before_sleep(retry_state: Any) -> None:
        await emit_sse(
            "waiting", typed_ref,
            {"attempt": retry_state.attempt_number, "detail": "Settlement processing"},
        )

    @retry(
        wait=wait_exponential(multiplier=1, min=2, max=10),
        stop=stop_after_delay(30),
        retry=retry_if_result(lambda r: r.status in ("pending", "processing")),
        before_sleep=_before_sleep,
    )
    async def _poll() -> Any:
        return await client.ledger_account_settlements.retrieve(settlement_id)

    return await _poll()


async def create_ledger_account_settlement(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Creating ledger account settlement")
    result = await client.ledger_account_settlements.create(
        **resolved,
        idempotency_key=idempotency_key,
    )

    if result.status in ("pending", "processing"):
        try:
            result = await _poll_settlement_status(client, result.id, typed_ref, emit_sse)
        except RetryError:
            logger.bind(ref=typed_ref).warning(
                "Settlement did not reach terminal state within timeout — proceeding"
            )

    return HandlerResult(
        created_id=result.id,
        resource_type="ledger_account_settlement",
        deletable=DELETABILITY["ledger_account_settlement"],
    )


async def create_ledger_account_balance_monitor(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Creating balance monitor")
    result = await client.ledger_account_balance_monitors.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="ledger_account_balance_monitor",
        deletable=DELETABILITY["ledger_account_balance_monitor"],
    )


async def create_ledger_account_statement(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Creating ledger account statement")
    result = await client.ledger_account_statements.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="ledger_account_statement",
        deletable=DELETABILITY["ledger_account_statement"],
    )


async def create_legal_entity_association(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Creating legal entity association")
    result = await client.legal_entity_associations.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="legal_entity_association",
        deletable=DELETABILITY["legal_entity_association"],
    )


async def create_transaction(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).warning(
        "Creating sandbox transaction directly — use IPDs for normal inbound simulation"
    )
    result = await client.transactions.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="transaction",
        deletable=DELETABILITY["transaction"],
    )


async def verify_external_account(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    logger.bind(ref=typed_ref).info("Initiating external account verification")
    ea_id = resolved.pop("external_account_ref")
    result = await client.external_accounts.verify(
        ea_id,
        originating_account_id=resolved["originating_account_id"],
        payment_type=resolved.get("payment_type", "rtp"),
        currency=resolved.get("currency"),
        priority=resolved.get("priority"),
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="verify_external_account",
        deletable=False,
    )


async def complete_verification(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    """Complete EA verification by reading micro-deposit PO amounts.

    In sandbox, POs complete instantly so amounts are readable via the API.
    """
    logger.bind(ref=typed_ref).info("Completing external account verification")
    ea_id = resolved.pop("external_account_ref")

    amounts: list[int] = []
    async for po in client.payment_orders.list(
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

    result = await client.external_accounts.complete_verification(
        ea_id, amounts=amounts,
    )
    return HandlerResult(
        created_id=result.id,
        resource_type="complete_verification",
        deletable=False,
    )


async def archive_resource(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    resource_type = resolved.pop("resource_type")
    resource_ref = resolved.pop("resource_ref")
    method = resolved.pop("archive_method", "delete")

    logger.bind(ref=typed_ref, target_type=resource_type, method=method).info(
        "Archiving resource"
    )

    if method == "delete":
        sdk_attr = SDK_ATTR_MAP.get(resource_type)
        if sdk_attr:
            await getattr(client, sdk_attr).delete(resource_ref)
    elif method == "archive":
        await client.ledger_transactions.update(resource_ref, status="archived")
    elif method == "request_closure":
        logger.bind(ref=typed_ref).info(
            "Requesting IA closure — this is a request, not an immediate close"
        )
        await client.internal_accounts.request_closure(resource_ref)

    return HandlerResult(
        created_id=resource_ref,
        resource_type="archive_resource",
        deletable=False,
    )


# ---------------------------------------------------------------------------
# Generic read/list operations (D2)
# ---------------------------------------------------------------------------


async def read_resource(
    client: AsyncModernTreasury,
    resource_type: str,
    resource_id: str,
) -> dict:
    """GET a single resource by type and ID."""
    sdk_attr = SDK_ATTR_MAP.get(resource_type)
    if not sdk_attr:
        raise ValueError(f"No SDK mapping for resource type '{resource_type}'")
    result = await getattr(client, sdk_attr).retrieve(resource_id)
    return result.model_dump() if hasattr(result, "model_dump") else dict(result)


async def list_resources(
    client: AsyncModernTreasury,
    resource_type: str,
    **filters,
) -> list[dict]:
    """List resources by type with optional filters."""
    sdk_attr = SDK_ATTR_MAP.get(resource_type)
    if not sdk_attr:
        raise ValueError(f"No SDK mapping for resource type '{resource_type}'")
    results = []
    async for item in getattr(client, sdk_attr).list(**filters):
        results.append(item.model_dump() if hasattr(item, "model_dump") else dict(item))
        if len(results) >= 100:
            break
    return results


# ---------------------------------------------------------------------------
# Dispatch table factory
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Update handlers (reconciled resources with edited payloads)
# ---------------------------------------------------------------------------

_STRIP_ON_UPDATE: dict[str, set[str]] = {
    "internal_account": {"connection_id", "currency"},
    "legal_entity": {"legal_entity_type"},
    "counterparty": set(),
    "ledger": set(),
    "ledger_account": {"currency", "ledger_id", "normal_balance"},
    "ledger_account_category": {"currency", "ledger_id", "normal_balance"},
    "external_account": {"counterparty_id"},
    "virtual_account": {"internal_account_id"},
    "expected_payment": set(),
}


async def _generic_update(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
    resolved: dict,
    *,
    resource_id: str,
    resource_type: str,
    sdk_attr: str,
    idempotency_key: str,
    typed_ref: str = "",
) -> HandlerResult:
    for key in _STRIP_ON_UPDATE.get(resource_type, set()):
        resolved.pop(key, None)
    logger.bind(ref=typed_ref).info("Updating {} {}", resource_type, resource_id[:12])
    sdk_resource = getattr(client, sdk_attr)
    result = await sdk_resource.update(resource_id, **resolved)
    return HandlerResult(
        created_id=result.id,
        resource_type=resource_type,
        deletable=DELETABILITY.get(resource_type, False),
    )


def build_update_dispatch(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
) -> dict[str, UpdateHandlerFn]:
    """Build the update handler dispatch table.

    Each handler calls ``.update(resource_id, ...)`` instead of ``.create()``,
    stripping immutable fields per resource type.
    """
    bind = functools.partial

    def _bind(resource_type: str, sdk_attr: str) -> UpdateHandlerFn:
        return bind(
            _generic_update, client, emit_sse,
            resource_type=resource_type, sdk_attr=sdk_attr,
        )

    return {
        "internal_account": _bind("internal_account", "internal_accounts"),
        "legal_entity": _bind("legal_entity", "legal_entities"),
        "counterparty": _bind("counterparty", "counterparties"),
        "ledger": _bind("ledger", "ledgers"),
        "ledger_account": _bind("ledger_account", "ledger_accounts"),
        "ledger_account_category": _bind(
            "ledger_account_category", "ledger_account_categories",
        ),
        "external_account": _bind("external_account", "external_accounts"),
        "virtual_account": _bind("virtual_account", "virtual_accounts"),
        "expected_payment": _bind("expected_payment", "expected_payments"),
    }


def build_handler_dispatch(
    client: AsyncModernTreasury,
    emit_sse: EmitFn,
) -> dict[str, HandlerFn]:
    """Build the handler dispatch table with client and emit_sse pre-bound.

    The engine calls each handler as:
        result = await handler(resolved, idempotency_key=..., typed_ref=...)
    """
    bind = functools.partial

    return {
        "connection": bind(create_connection, client, emit_sse),
        "legal_entity": bind(create_legal_entity, client, emit_sse),
        "legal_entity_association": bind(
            create_legal_entity_association, client, emit_sse
        ),
        "ledger": bind(create_ledger, client, emit_sse),
        "counterparty": bind(create_counterparty, client, emit_sse),
        "ledger_account": bind(create_ledger_account, client, emit_sse),
        "internal_account": bind(create_internal_account, client, emit_sse),
        "external_account": bind(create_external_account, client, emit_sse),
        "ledger_account_category": bind(
            create_ledger_account_category, client, emit_sse
        ),
        "ledger_account_settlement": bind(
            create_ledger_account_settlement, client, emit_sse
        ),
        "ledger_account_balance_monitor": bind(
            create_ledger_account_balance_monitor, client, emit_sse
        ),
        "ledger_account_statement": bind(
            create_ledger_account_statement, client, emit_sse
        ),
        "virtual_account": bind(create_virtual_account, client, emit_sse),
        "expected_payment": bind(create_expected_payment, client, emit_sse),
        "payment_order": bind(create_payment_order, client, emit_sse),
        "incoming_payment_detail": bind(
            create_incoming_payment_detail, client, emit_sse
        ),
        "ledger_transaction": bind(create_ledger_transaction, client, emit_sse),
        "transaction": bind(create_transaction, client, emit_sse),
        "return": bind(create_return, client, emit_sse),
        "reversal": bind(create_reversal, client, emit_sse),
        "category_membership": bind(create_category_membership, client, emit_sse),
        "nested_category": bind(create_nested_category, client, emit_sse),
        "transition_ledger_transaction": bind(
            transition_ledger_transaction, client, emit_sse
        ),
        "verify_external_account": bind(
            verify_external_account, client, emit_sse
        ),
        "complete_verification": bind(
            complete_verification, client, emit_sse
        ),
        "archive_resource": bind(archive_resource, client, emit_sse),
    }
