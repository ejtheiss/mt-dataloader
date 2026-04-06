"""Human labels for resource configs — engine-only (no handlers / UI stack)."""

from __future__ import annotations

from typing import Any

_NAME_ATTRS: dict[str, tuple[str, ...]] = {
    "connection": ("nickname",),
    "counterparty": ("name",),
    "external_account": ("party_name",),
    "internal_account": ("name",),
    "virtual_account": ("name",),
    "ledger": ("name",),
    "ledger_account": ("name",),
    "ledger_account_category": ("name",),
    "payment_order": ("description",),
    "expected_payment": ("description",),
    "incoming_payment_detail": ("description",),
    "ledger_transaction": ("description",),
    "return": ("reason",),
}


def extract_display_name(resource: Any) -> str:
    """Pull a human-meaningful label from a resource config."""
    rt = getattr(resource, "resource_type", "")
    attrs = _NAME_ATTRS.get(rt)
    if attrs:
        for attr in attrs:
            val = getattr(resource, attr, None)
            if val:
                return str(val)

    if rt == "legal_entity":
        le_type = getattr(resource, "legal_entity_type", "")
        if le_type == "business":
            bname = getattr(resource, "business_name", None)
            if bname:
                return str(bname)
        first = getattr(resource, "first_name", "") or ""
        last = getattr(resource, "last_name", "") or ""
        full = f"{first} {last}".strip()
        if full:
            return full

    return ""
