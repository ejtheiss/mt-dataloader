"""Org discovery — dynamically discover resources from a live MT org.

Discovers connections, internal accounts, ledgers, ledger accounts,
ledger account categories, legal entities, and counterparties.
Conditional fetch avoids pulling thousands of resources when the
config doesn't reference them.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from loguru import logger
from modern_treasury import AsyncModernTreasury

from dataloader.engine import all_resources

__all__ = [
    "DiscoveredConnection",
    "DiscoveredCounterparty",
    "DiscoveredInternalAccount",
    "DiscoveredLedger",
    "DiscoveredLedgerAccount",
    "DiscoveredLedgerAccountCategory",
    "DiscoveredLegalEntity",
    "DiscoveryResult",
    "_le_display_name",
    "_le_display_name_from_sdk",
    "discover_org",
]

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify_ref(name: str) -> str:
    """Convert a display name to a slug suitable for use as a ref key.

    ``"Gringotts Wizarding Bank"`` -> ``"gringotts_wizarding_bank"``
    """
    slug = _SLUG_RE.sub("_", name.lower()).strip("_")
    return slug or "unnamed"


def _assign_unique_refs(
    resource_type: str,
    names: list[str],
) -> list[str]:
    """Batch-assign deterministic typed refs with deduplication.

    All names for a resource type must be collected first so collisions are
    detected.  Duplicates get ``_2``, ``_3``, etc. suffixes.

    Returns typed refs like ``connection.gringotts_wizarding_bank``.
    """
    slug_counts: dict[str, int] = {}
    refs: list[str] = []

    for name in names:
        slug = _slugify_ref(name)
        slug_counts[slug] = slug_counts.get(slug, 0) + 1
        if slug_counts[slug] > 1:
            refs.append(f"{resource_type}.{slug}_{slug_counts[slug]}")
        else:
            refs.append(f"{resource_type}.{slug}")

    return refs


_T = TypeVar("_T")


async def _collect_named(
    items: AsyncIterator[_T],
    name_fn: Callable[[_T], str],
) -> tuple[list[_T], list[str]]:
    """Drain an MT async list iterator into objects and parallel display names."""
    objects: list[_T] = []
    names: list[str] = []
    async for item in items:
        objects.append(item)
        names.append(name_fn(item))
    return objects, names


# ---------------------------------------------------------------------------
# Discovered resource types
# ---------------------------------------------------------------------------


@dataclass
class DiscoveredConnection:
    id: str
    vendor_name: str
    vendor_id: str
    auto_ref: str = ""
    currencies: list[str] = field(default_factory=list)


@dataclass
class DiscoveredInternalAccount:
    id: str
    name: str | None
    currency: str
    connection_id: str
    connection_ref: str
    auto_ref: str = ""


@dataclass
class DiscoveredLedger:
    id: str
    name: str
    auto_ref: str = ""


@dataclass
class DiscoveredLedgerAccount:
    id: str
    name: str
    currency: str
    ledger_id: str
    ledger_ref: str
    normal_balance: str
    auto_ref: str = ""


@dataclass
class DiscoveredLedgerAccountCategory:
    id: str
    name: str
    currency: str
    ledger_id: str
    ledger_ref: str
    normal_balance: str
    auto_ref: str = ""


@dataclass
class DiscoveredLegalEntity:
    id: str
    legal_entity_type: str
    business_name: str | None
    first_name: str | None
    last_name: str | None
    status: str
    auto_ref: str = ""


@dataclass
class DiscoveredCounterparty:
    id: str
    name: str
    legal_entity_id: str | None
    account_count: int
    account_ids: list[str] = field(default_factory=list)
    auto_ref: str = ""


@dataclass
class DiscoveryResult:
    connections: list[DiscoveredConnection] = field(default_factory=list)
    internal_accounts: list[DiscoveredInternalAccount] = field(default_factory=list)
    ledgers: list[DiscoveredLedger] = field(default_factory=list)
    ledger_accounts: list[DiscoveredLedgerAccount] = field(default_factory=list)
    ledger_account_categories: list[DiscoveredLedgerAccountCategory] = field(default_factory=list)
    legal_entities: list[DiscoveredLegalEntity] = field(default_factory=list)
    counterparties: list[DiscoveredCounterparty] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------


def _le_display_name_from_sdk(le: Any) -> str:
    """Build a display name from an SDK LegalEntity response."""
    if le.legal_entity_type == "business":
        return le.business_name or f"le_{le.id[:8]}"
    return (
        f"{le.first_name or ''} {le.last_name or ''}".strip()
        or le.business_name
        or f"le_{le.id[:8]}"
    )


def _le_display_name(le: DiscoveredLegalEntity) -> str:
    """Build a display name from a ``DiscoveredLegalEntity``."""
    return (
        le.business_name
        or f"{le.first_name or ''} {le.last_name or ''}".strip()
        or f"le_{le.id[:8]}"
    )


# ---------------------------------------------------------------------------
# Main discovery function
# ---------------------------------------------------------------------------


async def discover_org(
    client: AsyncModernTreasury,
    config: Any | None = None,
) -> DiscoveryResult:
    """Discover existing resources from a live org.

    Connections, internal accounts, and ledgers are always fetched.
    Legal entities and counterparties are only fetched when the config
    contains those sections (conditional fetch -- avoids pulling thousands
    of resources in production orgs that don't need them).
    """
    result = DiscoveryResult()

    config_types: set[str] = set()
    if config is not None:
        for res in all_resources(config):
            config_types.add(res.resource_type)

    # --- Connections ---
    conn_objects, conn_names = await _collect_named(
        client.connections.list(),
        lambda c: c.vendor_name,
    )

    conn_refs = _assign_unique_refs("connection", conn_names)
    for conn, ref in zip(conn_objects, conn_refs):
        result.connections.append(
            DiscoveredConnection(
                id=conn.id,
                vendor_name=conn.vendor_name,
                vendor_id=conn.vendor_id,
                auto_ref=ref,
            )
        )

    if not result.connections:
        result.warnings.append(
            "No connections found in the live org. "
            "Your config will need to create one (sandbox-only, via connections section)."
        )

    conn_id_to_ref: dict[str, str] = {c.id: c.auto_ref for c in result.connections}

    # --- Internal Accounts ---
    def _ia_name(ia: Any) -> str:
        return ia.name or f"ia_{(ia.currency or 'usd').lower()}_{ia.id[:8]}"

    ia_objects, ia_names = await _collect_named(
        client.internal_accounts.list(),
        _ia_name,
    )

    ia_refs = _assign_unique_refs("internal_account", ia_names)
    for ia, ref in zip(ia_objects, ia_refs):
        conn_id = ia.connection.id if ia.connection else ""
        result.internal_accounts.append(
            DiscoveredInternalAccount(
                id=ia.id,
                name=ia.name,
                currency=ia.currency or "USD",
                connection_id=conn_id,
                connection_ref=conn_id_to_ref.get(conn_id, ""),
                auto_ref=ref,
            )
        )

    if not result.internal_accounts:
        result.warnings.append(
            "No internal accounts found in the live org. "
            "Your config will need to create one (requires a connection)."
        )

    conn_currencies: dict[str, set[str]] = {}
    for dia in result.internal_accounts:
        if dia.connection_id:
            conn_currencies.setdefault(dia.connection_id, set()).add(dia.currency.upper())
    for dc in result.connections:
        dc.currencies = sorted(conn_currencies.get(dc.id, set()))

    # --- Ledgers ---
    ledger_objects, ledger_names = await _collect_named(
        client.ledgers.list(),
        lambda lg: lg.name,
    )

    ledger_refs = _assign_unique_refs("ledger", ledger_names)
    for ledger, ref in zip(ledger_objects, ledger_refs):
        result.ledgers.append(
            DiscoveredLedger(
                id=ledger.id,
                name=ledger.name,
                auto_ref=ref,
            )
        )

    ledger_id_to_ref: dict[str, str] = {dl.id: dl.auto_ref for dl in result.ledgers}

    # --- Ledger Accounts (conditional) ---
    if "ledger_account" in config_types:
        la_objects: list[Any] = []
        la_names: list[str] = []
        for dl in result.ledgers:
            async for la in client.ledger_accounts.list(ledger_id=dl.id):
                la_objects.append(la)
                la_names.append(la.name or f"la_{la.id[:8]}")

        la_refs = _assign_unique_refs("ledger_account", la_names)
        for la, ref in zip(la_objects, la_refs):
            _currency = getattr(la, "currency", None) or "USD"
            if _currency == "USD" and la.balances and la.balances.pending_balance:
                _currency = la.balances.pending_balance.currency or "USD"
            _normal_balance = getattr(la, "normal_balance", None) or "credit"
            result.ledger_accounts.append(
                DiscoveredLedgerAccount(
                    id=la.id,
                    name=la.name or "",
                    currency=_currency,
                    ledger_id=la.ledger_id,
                    ledger_ref=ledger_id_to_ref.get(la.ledger_id, ""),
                    normal_balance=_normal_balance,
                    auto_ref=ref,
                )
            )

    # --- Ledger Account Categories (conditional) ---
    if "ledger_account_category" in config_types:
        lac_objects: list[Any] = []
        lac_names: list[str] = []
        for dl in result.ledgers:
            async for lac in client.ledger_account_categories.list(ledger_id=dl.id):
                lac_objects.append(lac)
                lac_names.append(lac.name or f"lac_{lac.id[:8]}")

        lac_refs = _assign_unique_refs("ledger_account_category", lac_names)
        for lac, ref in zip(lac_objects, lac_refs):
            _lac_currency = getattr(lac, "currency", None) or "USD"
            if _lac_currency == "USD" and lac.balances and lac.balances.pending_balance:
                _lac_currency = lac.balances.pending_balance.currency or "USD"
            _lac_normal_balance = getattr(lac, "normal_balance", None) or "credit"
            result.ledger_account_categories.append(
                DiscoveredLedgerAccountCategory(
                    id=lac.id,
                    name=lac.name or "",
                    currency=_lac_currency,
                    ledger_id=lac.ledger_id,
                    ledger_ref=ledger_id_to_ref.get(lac.ledger_id, ""),
                    normal_balance=_lac_normal_balance,
                    auto_ref=ref,
                )
            )

    # --- Legal Entities (conditional) ---
    if "legal_entity" in config_types:
        le_objects, le_names = await _collect_named(
            client.legal_entities.list(),
            _le_display_name_from_sdk,
        )

        le_refs = _assign_unique_refs("legal_entity", le_names)
        for le, ref in zip(le_objects, le_refs):
            result.legal_entities.append(
                DiscoveredLegalEntity(
                    id=le.id,
                    legal_entity_type=le.legal_entity_type or "business",
                    business_name=le.business_name,
                    first_name=le.first_name,
                    last_name=le.last_name,
                    status=le.status or "unknown",
                    auto_ref=ref,
                )
            )

    # --- Counterparties (conditional) ---
    if "counterparty" in config_types:
        cp_objects, cp_names = await _collect_named(
            client.counterparties.list(),
            lambda cp: cp.name or f"cp_{cp.id[:8]}",
        )

        cp_refs = _assign_unique_refs("counterparty", cp_names)
        for cp, ref in zip(cp_objects, cp_refs):
            acct_ids = [a.id for a in (cp.accounts or []) if a.id]
            result.counterparties.append(
                DiscoveredCounterparty(
                    id=cp.id,
                    name=cp.name or f"cp_{cp.id[:8]}",
                    legal_entity_id=cp.legal_entity_id,
                    account_count=len(cp.accounts) if cp.accounts else 0,
                    account_ids=acct_ids,
                    auto_ref=ref,
                )
            )

    logger.bind(
        connections=len(result.connections),
        internal_accounts=len(result.internal_accounts),
        ledgers=len(result.ledgers),
        ledger_accounts=len(result.ledger_accounts),
        ledger_account_categories=len(result.ledger_account_categories),
        legal_entities=len(result.legal_entities),
        counterparties=len(result.counterparties),
        warnings=len(result.warnings),
    ).info("Org discovery complete")

    return result
