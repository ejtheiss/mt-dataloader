"""Incoming payment detail payload after ``resolve_refs``."""

from __future__ import annotations

from typing import Any

from pydantic import RootModel


class ResolvedIncomingPaymentDetailPayload(RootModel[dict[str, Any]]):
    """JSON object for ``incoming_payment_details.create_async``."""
