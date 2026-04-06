"""Map MT webhook payload ``data`` to ``(run_id, typed_ref)`` via resource id index."""

from __future__ import annotations

from typing import Any

_CORRELATION_FIELDS = (
    "internal_account_id",
    "originating_account_id",
    "receiving_account_id",
    "counterparty_id",
    "legal_entity_id",
    "ledger_transaction_id",
    "ledger_account_id",
    "batch_id",
    "returnable_id",
    "virtual_account_id",
    "ledgerable_id",
)


def correlate_webhook_data(
    data: Any,
    index: dict[str, tuple[str, str]],
) -> tuple[str | None, str | None]:
    """Match a webhook payload to a run via the correlation index."""
    if not isinstance(data, dict):
        return None, None
    primary = data.get("id", "")
    if primary and primary in index:
        return index[primary]
    for field_name in _CORRELATION_FIELDS:
        val = data.get(field_name)
        if isinstance(val, str) and val in index:
            return index[val]
    return None, None
