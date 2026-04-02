from __future__ import annotations

from typing import Any

from loguru import logger
from modern_treasury import AsyncModernTreasury
from modern_treasury._exceptions import APIStatusError
from modern_treasury.types.connection import Connection
from tenacity import RetryError, retry, retry_if_exception, retry_if_result

from models import HandlerResult

from .constants import (
    DELETABILITY,
    SDK_ATTR_MAP,
    TENACITY_STOP_30,
    TENACITY_STOP_60,
    TENACITY_WAIT_EXP_2_5,
    TENACITY_WAIT_EXP_2_10,
    EmitFn,
)


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
        wait=TENACITY_WAIT_EXP_2_10,
        stop=TENACITY_STOP_30,
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
        wait=TENACITY_WAIT_EXP_2_10,
        stop=TENACITY_STOP_60,
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
            logger.bind(ref=typed_ref).info("Connection already exists, looking up by entity_id")
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
        wait=TENACITY_WAIT_EXP_2_10,
        stop=TENACITY_STOP_60,
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
                client,
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
    conn_id = resolved.get("connection_id")
    if not conn_id or not str(conn_id).strip():
        raise ValueError(
            f"{typed_ref}: internal account is missing connection_id after ref "
            f"resolution — check connection reconciliation and $ref:connection.*"
        )
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
        wait=TENACITY_WAIT_EXP_2_5,
        stop=TENACITY_STOP_30,
        retry=retry_if_exception(
            lambda exc: (
                isinstance(exc, APIStatusError) and exc.status_code in (429, 500, 502, 503, 504)
            )
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
        logger.bind(ref=typed_ref, po_status=po.status).info("PO reached reversible state")
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

    logger.bind(ref=typed_ref).info("Adding ledger account to category")

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
            logger.bind(ref=typed_ref).info("Sub-category already nested — treating as success")
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
            "waiting",
            typed_ref,
            {"attempt": retry_state.attempt_number, "detail": "Settlement processing"},
        )

    @retry(
        wait=TENACITY_WAIT_EXP_2_10,
        stop=TENACITY_STOP_30,
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
        ea_id,
        amounts=amounts,
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

    logger.bind(ref=typed_ref, target_type=resource_type, method=method).info("Archiving resource")

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
