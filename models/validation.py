"""Pre-compile advisory diagnostics and validation constants.

ConfigDiagnostic is for parse-time warnings (before flow compilation).
FlowDiagnostic (in flow_validator.py) is for post-compile analysis.
Both are non-blocking — warnings, not hard errors.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from typing import Literal

__all__ = [
    "ConfigDiagnostic",
    "KNOWN_PAYMENT_TYPES",
    "RAIL_CURRENCIES",
    "suggest_payment_type",
    "validate_currency_rail",
]


@dataclass(frozen=True)
class ConfigDiagnostic:
    """Pre-compile advisory diagnostic."""

    rule_id: str
    severity: Literal["error", "warning", "info"]
    resource_ref: str
    field: str | None
    message: str
    suggestion: str | None = None


KNOWN_PAYMENT_TYPES: frozenset[str] = frozenset({
    "ach", "au_becs", "bacs", "base", "book", "card", "chats", "check",
    "cross_border", "dk_nets", "eft", "ethereum", "gb_fps", "hu_ics",
    "interac", "masav", "mx_ccen", "neft", "nics", "nz_becs",
    "pl_elixir", "polygon", "provxchange", "ro_sent", "rtp",
    "se_bankgirot", "sen", "sepa", "sg_giro", "sic", "signet",
    "sknbi", "solana", "wire", "zengin",
})

RAIL_CURRENCIES: dict[str, frozenset[str]] = {
    "ach": frozenset({"USD"}),
    "wire": frozenset({"USD", "CAD", "EUR", "GBP"}),
    "rtp": frozenset({"USD"}),
    "book": frozenset(),  # any currency
    "sepa": frozenset({"EUR"}),
    "bacs": frozenset({"GBP"}),
    "eft": frozenset({"CAD"}),
    "au_becs": frozenset({"AUD"}),
    "nz_becs": frozenset({"NZD"}),
    "check": frozenset({"USD"}),
    "cross_border": frozenset(),  # multi-currency by nature
}


def suggest_payment_type(unknown: str) -> str | None:
    """Return closest known payment type via fuzzy match, or None."""
    matches = difflib.get_close_matches(unknown, sorted(KNOWN_PAYMENT_TYPES), n=1, cutoff=0.6)
    return matches[0] if matches else None


def validate_currency_rail(
    payment_type: str,
    currency: str | None,
    resource_ref: str,
) -> ConfigDiagnostic | None:
    """Check currency/rail compatibility. Returns a diagnostic or None."""
    if not currency or payment_type not in RAIL_CURRENCIES:
        return None
    valid = RAIL_CURRENCIES[payment_type]
    if not valid:
        return None
    if currency.upper() not in valid:
        return ConfigDiagnostic(
            rule_id="PAYMENT_005",
            severity="warning",
            resource_ref=resource_ref,
            field="currency",
            message=(
                f"Currency '{currency}' may not be supported on '{payment_type}' rail. "
                f"Expected: {sorted(valid)}"
            ),
            suggestion=f"Change currency to one of {sorted(valid)} or use a different payment type.",
        )
    return None
