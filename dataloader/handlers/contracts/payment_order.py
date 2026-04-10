"""Payment order payload after ``resolve_refs`` (extend fields as invariants tighten)."""

from __future__ import annotations

from typing import Any

from pydantic import RootModel


class ResolvedPaymentOrderPayload(RootModel[dict[str, Any]]):
    """JSON object sent to ``payment_orders.create``; validates mapping shape only."""
