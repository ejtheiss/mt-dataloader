"""Constants and helpers shared with ``flow_validator`` (payment type hints).

``FlowDiagnostic`` / rule output live in ``flow_validator.py``.
"""

from __future__ import annotations

import difflib

__all__ = [
    "KNOWN_PAYMENT_TYPES",
    "suggest_payment_type",
]

KNOWN_PAYMENT_TYPES: frozenset[str] = frozenset({
    "ach", "au_becs", "bacs", "base", "book", "card", "chats", "check",
    "cross_border", "dk_nets", "eft", "ethereum", "gb_fps", "hu_ics",
    "interac", "masav", "mx_ccen", "neft", "nics", "nz_becs",
    "pl_elixir", "polygon", "provxchange", "ro_sent", "rtp",
    "se_bankgirot", "sen", "sepa", "sg_giro", "sic", "signet",
    "sknbi", "solana", "wire", "zengin",
})


def suggest_payment_type(unknown: str) -> str | None:
    """Return closest known payment type via fuzzy match, or None."""
    matches = difflib.get_close_matches(unknown, sorted(KNOWN_PAYMENT_TYPES), n=1, cutoff=0.6)
    return matches[0] if matches else None
