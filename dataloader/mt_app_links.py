"""Deep links into the Modern Treasury web app (customer dashboard).

Docs URLs live in ``mt_doc_links.MT_DOCS``; this module maps created resource
types to ``https://app.moderntreasury.com/<segment>/<id>`` where applicable.
"""

from __future__ import annotations

from dataloader.handlers.constants import SDK_ATTR_MAP

MT_APP_BASE = "https://app.moderntreasury.com"

# SDK maps ``reversal`` to ``payment_orders`` for API calls; reversals have
# their own ids and may not resolve correctly under /payment_orders/{id}.
_NO_APP_URL_TYPES = frozenset(
    {
        "reversal",
        "verify_external_account",
        "complete_verification",
        "archive_resource",
        "transition_ledger_transaction",
        "legal_entity_association",
    }
)


def mt_app_resource_url(resource_type: str, created_id: str) -> str | None:
    """Return a dashboard URL for *created_id*, or ``None`` if unknown or not linkable."""
    if not created_id or created_id == "SKIPPED":
        return None
    if resource_type in _NO_APP_URL_TYPES:
        return None
    segment = SDK_ATTR_MAP.get(resource_type)
    if not segment:
        return None
    return f"{MT_APP_BASE}/{segment}/{created_id}"
