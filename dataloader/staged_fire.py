"""Staged-resource types that can be POST-fired from the run detail UI.

Shared by ``dataloader.engine`` (dry-run validation) and
``dataloader.webhooks.runs_staged`` (``_FIRE_DISPATCH``). Keep keys in sync.
"""

from __future__ import annotations

FIREABLE_TYPES: frozenset[str] = frozenset(
    {
        "payment_order",
        "expected_payment",
        "ledger_transaction",
        "incoming_payment_detail",
    }
)
