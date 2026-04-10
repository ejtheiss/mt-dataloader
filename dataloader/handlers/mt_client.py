"""Modern Treasury SDK wrapper: polling and shared HTTP access (handlers boundary)."""

from __future__ import annotations

from typing import Any

from modern_treasury import AsyncModernTreasury
from tenacity import retry, retry_if_result

from dataloader.handlers.constants import (
    TENACITY_STOP_30,
    TENACITY_STOP_60,
    TENACITY_WAIT_EXP_2_10,
    EmitFn,
)

_PO_REVERSIBLE_STATUSES = frozenset({"approved", "sent", "completed"})


class MTClient:
    """Thin facade over ``AsyncModernTreasury`` — use ``.sdk`` for direct resource access."""

    __slots__ = ("_sdk",)

    def __init__(self, sdk: AsyncModernTreasury) -> None:
        self._sdk = sdk

    @property
    def sdk(self) -> AsyncModernTreasury:
        return self._sdk

    async def poll_ipd_until_completed(self, ipd_id: str, typed_ref: str, emit_sse: EmitFn) -> Any:
        """Poll an IPD until status == 'completed'."""

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
            return await self._sdk.incoming_payment_details.retrieve(ipd_id)

        return await _poll()

    async def poll_po_until_reversible(self, po_id: str, typed_ref: str, emit_sse: EmitFn) -> Any:
        """Poll a Payment Order until it reaches a reversible state."""

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
            return await self._sdk.payment_orders.retrieve(po_id)

        return await _poll()

    async def poll_legal_entity_until_settled(
        self, le_id: str, typed_ref: str, emit_sse: EmitFn
    ) -> Any:
        """Poll a Legal Entity until status is ``active`` or ``denied``."""

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
            return await self._sdk.legal_entities.retrieve(le_id)

        return await _poll()

    async def poll_settlement_until_terminal(
        self, settlement_id: str, typed_ref: str, emit_sse: EmitFn
    ) -> Any:
        """Poll a settlement until it leaves pending/processing."""

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
            return await self._sdk.ledger_account_settlements.retrieve(settlement_id)

        return await _poll()
