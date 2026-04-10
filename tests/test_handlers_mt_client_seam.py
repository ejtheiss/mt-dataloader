"""Lock the service + MTClient seam (Rails-style handler layout)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from dataloader.handlers.mt_client import MTClient
from dataloader.handlers.services.payment_orders.create import call as create_payment_order


@pytest.mark.asyncio
async def test_payment_order_service_delegates_to_sdk() -> None:
    created = MagicMock()
    created.id = "po-test-1"
    created.ledger_transaction_id = None

    sdk = MagicMock()
    sdk.payment_orders = MagicMock()
    sdk.payment_orders.create = AsyncMock(return_value=created)

    mt = MTClient(sdk)
    emit_sse = AsyncMock()
    resolved: dict = {"direction": "credit", "type": "ach"}

    result = await create_payment_order(
        mt, emit_sse, resolved, idempotency_key="idem-1", typed_ref="ledger.po-0"
    )

    assert result.created_id == "po-test-1"
    assert result.resource_type == "payment_order"
    sdk.payment_orders.create.assert_awaited_once()
    call_kw = sdk.payment_orders.create.await_args
    assert call_kw.kwargs["idempotency_key"] == "idem-1"
    assert call_kw.kwargs["direction"] == "credit"
