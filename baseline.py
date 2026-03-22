"""Sandbox baseline state management for the Modern Treasury Dataloader.

Owns four responsibilities:
1. Schema — Pydantic models for baseline.yaml (pre-existing resources)
2. Registry seeding — populate RefRegistry with baseline ref → UUID mappings
3. Preflight validation — verify declared resources exist in the live MT org
4. Org discovery — dynamically discover connections, IAs, ledgers from live org

Pre-existing resources (connections, internal accounts, etc.) are declared in
baseline.yaml with known UUIDs.  JSON configs reference them via $ref: strings.
The discovery path replaces manual baseline authoring for connected scenarios.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml
from loguru import logger
from modern_treasury import AsyncModernTreasury, NotFoundError
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from engine import RefRegistry, all_resources, typed_ref_for

__all__ = [
    "BaselineConnection",
    "BaselineInternalAccount",
    "BaselineLedger",
    "BaselineLegalEntity",
    "BaselineCounterparty",
    "BaselineConfig",
    "load_baseline",
    "seed_registry",
    "run_preflight",
    "PreflightIssue",
    "PreflightResult",
    "IssueLevel",
    # Discovery
    "DiscoveredConnection",
    "DiscoveredInternalAccount",
    "DiscoveredLedger",
    "DiscoveredLedgerAccount",
    "DiscoveredLedgerAccountCategory",
    "DiscoveredLegalEntity",
    "DiscoveredCounterparty",
    "DiscoveryResult",
    "discover_org",
    "baseline_from_discovery",
    # Reconciliation
    "ReconciledResource",
    "ReconciliationResult",
    "reconcile_config",
]

# ---------------------------------------------------------------------------
# Baseline YAML schema
# ---------------------------------------------------------------------------


class BaselineConnection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(..., description="Typed ref, e.g. 'connection.gringotts'")
    id: str = Field(..., min_length=1, description="Known UUID from the sandbox")
    vendor_name: str = Field(
        ..., min_length=1, description="SDK field: Connection.vendor_name"
    )

    @field_validator("ref")
    @classmethod
    def _ref_must_be_typed(cls, v: str) -> str:
        if not v.startswith("connection."):
            raise ValueError(
                f"Connection ref must start with 'connection.', got '{v}'"
            )
        return v


class BaselineInternalAccount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(...)
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    connection_ref: str | None = None

    @field_validator("ref")
    @classmethod
    def _ref_must_be_typed(cls, v: str) -> str:
        if not v.startswith("internal_account."):
            raise ValueError(
                f"Internal account ref must start with 'internal_account.', got '{v}'"
            )
        return v


class BaselineLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(...)
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)

    @field_validator("ref")
    @classmethod
    def _ref_must_be_typed(cls, v: str) -> str:
        if not v.startswith("ledger."):
            raise ValueError(f"Ledger ref must start with 'ledger.', got '{v}'")
        return v


class BaselineLegalEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(...)
    id: str = Field(..., min_length=1)
    business_name: str = Field(
        ...,
        min_length=1,
        description="SDK field: LegalEntity.business_name (no single 'name' field)",
    )
    display_name: str = ""

    @field_validator("ref")
    @classmethod
    def _ref_must_be_typed(cls, v: str) -> str:
        if not v.startswith("legal_entity."):
            raise ValueError(
                f"Legal entity ref must start with 'legal_entity.', got '{v}'"
            )
        return v


class BaselineCounterparty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ref: str = Field(...)
    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)

    @field_validator("ref")
    @classmethod
    def _ref_must_be_typed(cls, v: str) -> str:
        if not v.startswith("counterparty."):
            raise ValueError(
                f"Counterparty ref must start with 'counterparty.', got '{v}'"
            )
        return v


class BaselineConfig(BaseModel):
    """Top-level baseline schema parsed from baseline.yaml."""

    model_config = ConfigDict(extra="forbid")

    connections: list[BaselineConnection] = []
    internal_accounts: list[BaselineInternalAccount] = []
    ledgers: list[BaselineLedger] = []
    legal_entities: list[BaselineLegalEntity] = []
    counterparties: list[BaselineCounterparty] = []

    @model_validator(mode="after")
    def _refs_must_be_unique(self) -> BaselineConfig:
        seen: dict[str, str] = {}
        all_entries = [
            ("connections", self.connections),
            ("internal_accounts", self.internal_accounts),
            ("ledgers", self.ledgers),
            ("legal_entities", self.legal_entities),
            ("counterparties", self.counterparties),
        ]
        for section_name, entries in all_entries:
            for entry in entries:
                if entry.ref in seen:
                    raise ValueError(
                        f"Duplicate ref '{entry.ref}' in baseline: "
                        f"appears in both '{seen[entry.ref]}' and '{section_name}'"
                    )
                seen[entry.ref] = section_name
        return self


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_baseline(path: str | Path) -> BaselineConfig:
    """Load and validate baseline.yaml.

    Raises FileNotFoundError if the file doesn't exist.
    Raises pydantic.ValidationError if the YAML doesn't match the schema.
    Raises yaml.YAMLError on invalid YAML syntax.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Baseline file not found: {file_path}")

    raw = yaml.safe_load(file_path.read_text(encoding="utf-8"))
    if raw is None:
        raw = {}

    return BaselineConfig(**raw)


# ---------------------------------------------------------------------------
# Registry seeding
# ---------------------------------------------------------------------------


def seed_registry(baseline: BaselineConfig, registry: RefRegistry) -> set[str]:
    """Seed the registry with all baseline refs.

    Returns the set of baseline typed refs (passed to ``engine.dry_run``
    for ref existence validation).
    """
    baseline_refs: set[str] = set()

    for conn in baseline.connections:
        registry.register(conn.ref, conn.id)
        baseline_refs.add(conn.ref)

    for ia in baseline.internal_accounts:
        registry.register(ia.ref, ia.id)
        baseline_refs.add(ia.ref)

    for ledger in baseline.ledgers:
        registry.register(ledger.ref, ledger.id)
        baseline_refs.add(ledger.ref)

    for le in baseline.legal_entities:
        registry.register(le.ref, le.id)
        baseline_refs.add(le.ref)

    for cp in baseline.counterparties:
        registry.register(cp.ref, cp.id)
        baseline_refs.add(cp.ref)

    return baseline_refs


# ---------------------------------------------------------------------------
# Preflight validation
# ---------------------------------------------------------------------------


class IssueLevel(Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class PreflightIssue:
    """Single validation issue found during preflight."""

    resource_type: str
    ref: str
    level: IssueLevel
    message: str


@dataclass
class PreflightResult:
    """Aggregate result of preflight validation."""

    passed: bool = True
    issues: list[PreflightIssue] = field(default_factory=list)

    def add(self, issue: PreflightIssue) -> None:
        self.issues.append(issue)
        if issue.level == IssueLevel.ERROR:
            self.passed = False

    @property
    def errors(self) -> list[PreflightIssue]:
        return [i for i in self.issues if i.level == IssueLevel.ERROR]

    @property
    def warnings(self) -> list[PreflightIssue]:
        return [i for i in self.issues if i.level == IssueLevel.WARNING]


async def run_preflight(
    client: AsyncModernTreasury,
    baseline: BaselineConfig,
) -> PreflightResult:
    """Validate every baseline resource against the live MT org.

    Uses retrieve() for resources that support it (one call per resource).
    Connections use list() since there's no retrieve endpoint.

    Errors = resource missing or inaccessible.
    Warnings = resource exists but name doesn't match.
    """
    result = PreflightResult()

    await _validate_connections(client, baseline, result)

    await _validate_by_retrieve(
        entries=baseline.internal_accounts,
        retrieve_fn=client.internal_accounts.retrieve,
        resource_type="internal_account",
        name_getter=lambda live: live.name or "(no name)",
        entry_name_getter=lambda e: e.name,
        result=result,
    )

    await _validate_by_retrieve(
        entries=baseline.ledgers,
        retrieve_fn=client.ledgers.retrieve,
        resource_type="ledger",
        name_getter=lambda live: live.name or "(no name)",
        entry_name_getter=lambda e: e.name,
        result=result,
    )

    await _validate_by_retrieve(
        entries=baseline.legal_entities,
        retrieve_fn=client.legal_entities.retrieve,
        resource_type="legal_entity",
        name_getter=lambda live: (
            live.business_name
            or f"{live.first_name or ''} {live.last_name or ''}".strip()
            or "(no name)"
        ),
        entry_name_getter=lambda e: e.display_name or e.business_name,
        result=result,
    )

    await _validate_by_retrieve(
        entries=baseline.counterparties,
        retrieve_fn=client.counterparties.retrieve,
        resource_type="counterparty",
        name_getter=lambda live: live.name or "(no name)",
        entry_name_getter=lambda e: e.name,
        result=result,
    )

    if result.passed:
        total = (
            len(baseline.connections)
            + len(baseline.internal_accounts)
            + len(baseline.ledgers)
            + len(baseline.legal_entities)
            + len(baseline.counterparties)
        )
        logger.info(f"Preflight passed: {total} baseline resources verified")
    else:
        logger.bind(errors=len(result.errors)).warning("Preflight failed")

    return result


# ---------------------------------------------------------------------------
# Preflight helpers
# ---------------------------------------------------------------------------


async def _validate_connections(
    client: AsyncModernTreasury,
    baseline: BaselineConfig,
    result: PreflightResult,
) -> None:
    """Connections have no retrieve() endpoint — list and filter by ID."""
    if not baseline.connections:
        return

    live_connections: dict[str, str] = {}
    async for conn in client.connections.list():
        live_connections[conn.id] = conn.vendor_name

    for entry in baseline.connections:
        if entry.id not in live_connections:
            result.add(
                PreflightIssue(
                    resource_type="connection",
                    ref=entry.ref,
                    level=IssueLevel.ERROR,
                    message=(
                        f"Connection '{entry.ref}' (id={entry.id}) "
                        f"not found in org"
                    ),
                )
            )
        elif live_connections[entry.id] != entry.vendor_name:
            result.add(
                PreflightIssue(
                    resource_type="connection",
                    ref=entry.ref,
                    level=IssueLevel.WARNING,
                    message=(
                        f"Connection '{entry.ref}' vendor_name mismatch: "
                        f"expected '{entry.vendor_name}', "
                        f"got '{live_connections[entry.id]}'"
                    ),
                )
            )


async def _validate_by_retrieve(
    entries: list[Any],
    retrieve_fn: Callable[..., Awaitable[Any]],
    resource_type: str,
    name_getter: Callable[[Any], str],
    entry_name_getter: Callable[[Any], str],
    result: PreflightResult,
) -> None:
    """Generic retrieve-and-check-name validator.

    Works for internal_accounts, ledgers, legal_entities, counterparties —
    any resource with a retrieve(id) endpoint and a name-like field.
    """
    for entry in entries:
        try:
            live = await retrieve_fn(entry.id)
            live_name = name_getter(live)
            expected_name = entry_name_getter(entry)
            if live_name != expected_name:
                result.add(
                    PreflightIssue(
                        resource_type=resource_type,
                        ref=entry.ref,
                        level=IssueLevel.WARNING,
                        message=(
                            f"{resource_type} '{entry.ref}' name mismatch: "
                            f"expected '{expected_name}', got '{live_name}'"
                        ),
                    )
                )
        except NotFoundError:
            result.add(
                PreflightIssue(
                    resource_type=resource_type,
                    ref=entry.ref,
                    level=IssueLevel.ERROR,
                    message=(
                        f"{resource_type} '{entry.ref}' (id={entry.id}) "
                        f"not found in org"
                    ),
                )
            )


# ---------------------------------------------------------------------------
# Org Discovery — dynamic baseline from live org
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify_ref(name: str) -> str:
    """Convert a display name to a slug suitable for use as a ref key.

    ``"Gringotts Wizarding Bank"`` → ``"gringotts_wizarding_bank"``
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


async def discover_org(
    client: AsyncModernTreasury,
    config: Any | None = None,
) -> DiscoveryResult:
    """Discover existing resources from a live org.

    Connections, internal accounts, and ledgers are always fetched.
    Legal entities and counterparties are only fetched when the config
    contains those sections (conditional fetch — avoids pulling thousands
    of resources in production orgs that don't need them).
    """
    result = DiscoveryResult()

    config_types: set[str] = set()
    if config is not None:
        for res in all_resources(config):
            config_types.add(res.resource_type)

    # --- Connections ---
    conn_objects: list[Any] = []
    conn_names: list[str] = []
    async for conn in client.connections.list():
        conn_objects.append(conn)
        conn_names.append(conn.vendor_name)

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

    conn_id_to_ref: dict[str, str] = {
        c.id: c.auto_ref for c in result.connections
    }

    # --- Internal Accounts ---
    ia_objects: list[Any] = []
    ia_names: list[str] = []
    async for ia in client.internal_accounts.list():
        ia_objects.append(ia)
        display_name = ia.name or f"ia_{(ia.currency or 'usd').lower()}_{ia.id[:8]}"
        ia_names.append(display_name)

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

    # Enrich connections with the currencies of their associated IAs
    conn_currencies: dict[str, set[str]] = {}
    for dia in result.internal_accounts:
        if dia.connection_id:
            conn_currencies.setdefault(dia.connection_id, set()).add(dia.currency.upper())
    for dc in result.connections:
        dc.currencies = sorted(conn_currencies.get(dc.id, set()))

    # --- Ledgers ---
    ledger_objects: list[Any] = []
    ledger_names: list[str] = []
    async for ledger in client.ledgers.list():
        ledger_objects.append(ledger)
        ledger_names.append(ledger.name)

    ledger_refs = _assign_unique_refs("ledger", ledger_names)
    for ledger, ref in zip(ledger_objects, ledger_refs):
        result.ledgers.append(
            DiscoveredLedger(
                id=ledger.id,
                name=ledger.name,
                auto_ref=ref,
            )
        )

    ledger_id_to_ref: dict[str, str] = {
        dl.id: dl.auto_ref for dl in result.ledgers
    }

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
            result.ledger_accounts.append(
                DiscoveredLedgerAccount(
                    id=la.id,
                    name=la.name or "",
                    currency=la.currency or "USD",
                    ledger_id=la.ledger_id,
                    ledger_ref=ledger_id_to_ref.get(la.ledger_id, ""),
                    normal_balance=la.normal_balance or "credit",
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
            result.ledger_account_categories.append(
                DiscoveredLedgerAccountCategory(
                    id=lac.id,
                    name=lac.name or "",
                    currency=lac.currency or "USD",
                    ledger_id=lac.ledger_id,
                    ledger_ref=ledger_id_to_ref.get(lac.ledger_id, ""),
                    normal_balance=lac.normal_balance or "credit",
                    auto_ref=ref,
                )
            )

    # --- Legal Entities (conditional) ---
    if "legal_entity" in config_types:
        le_objects: list[Any] = []
        le_names: list[str] = []
        async for le in client.legal_entities.list():
            le_objects.append(le)
            le_names.append(_le_display_name_from_sdk(le))

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
        cp_objects: list[Any] = []
        cp_names: list[str] = []
        async for cp in client.counterparties.list():
            cp_objects.append(cp)
            cp_names.append(cp.name or f"cp_{cp.id[:8]}")

        cp_refs = _assign_unique_refs("counterparty", cp_names)
        for cp, ref in zip(cp_objects, cp_refs):
            result.counterparties.append(
                DiscoveredCounterparty(
                    id=cp.id,
                    name=cp.name or f"cp_{cp.id[:8]}",
                    legal_entity_id=cp.legal_entity_id,
                    account_count=len(cp.accounts) if cp.accounts else 0,
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


def baseline_from_discovery(discovery: DiscoveryResult) -> BaselineConfig:
    """Convert a ``DiscoveryResult`` into the same ``BaselineConfig`` type
    that ``seed_registry()`` and ``run_preflight()`` expect.

    This is the bridge between live discovery and the existing baseline
    infrastructure — the rest of the system doesn't know or care whether
    the baseline came from a YAML file or live discovery.
    """
    connections = [
        BaselineConnection(
            ref=c.auto_ref,
            id=c.id,
            vendor_name=c.vendor_name,
        )
        for c in discovery.connections
    ]

    internal_accounts = [
        BaselineInternalAccount(
            ref=ia.auto_ref,
            id=ia.id,
            name=ia.name or f"ia_{ia.currency.lower()}_{ia.id[:8]}",
            connection_ref=ia.connection_ref or None,
        )
        for ia in discovery.internal_accounts
    ]

    ledgers = [
        BaselineLedger(
            ref=lg.auto_ref,
            id=lg.id,
            name=lg.name,
        )
        for lg in discovery.ledgers
    ]

    legal_entities = [
        BaselineLegalEntity(
            ref=le.auto_ref,
            id=le.id,
            business_name=_le_display_name(le),
            display_name=_le_display_name(le),
        )
        for le in discovery.legal_entities
    ]

    counterparties = [
        BaselineCounterparty(
            ref=cp.auto_ref,
            id=cp.id,
            name=cp.name or f"cp_{cp.id[:8]}",
        )
        for cp in discovery.counterparties
    ]

    return BaselineConfig(
        connections=connections,
        internal_accounts=internal_accounts,
        ledgers=ledgers,
        legal_entities=legal_entities,
        counterparties=counterparties,
    )


# ---------------------------------------------------------------------------
# Reconciliation — match config resources to discovered org resources
# ---------------------------------------------------------------------------

from models import (
    ConnectionConfig,
    CounterpartyConfig,
    DataLoaderConfig,
    InternalAccountConfig,
    LedgerAccountCategoryConfig,
    LedgerAccountConfig,
    LedgerConfig,
    LegalEntityConfig,
    _BaseResourceConfig,
)


@dataclass
class ReconciledResource:
    config_ref: str
    config_resource: _BaseResourceConfig
    discovered_id: str
    discovered_name: str
    match_reason: str
    use_existing: bool = True
    duplicates: list[dict] | None = None


@dataclass
class ReconciliationResult:
    matches: list[ReconciledResource] = field(default_factory=list)
    unmatched_config: list[str] = field(default_factory=list)
    unmatched_discovered: list[str] = field(default_factory=list)


def _pick_best_le(candidates: list[DiscoveredLegalEntity]) -> DiscoveredLegalEntity:
    """Prefer active LEs over other statuses when auto-selecting."""
    return next((c for c in candidates if c.status == "active"), candidates[0])


def reconcile_config(
    config: DataLoaderConfig,
    discovery: DiscoveryResult,
) -> ReconciliationResult:
    """Match config-defined resources against discovered org resources.

    Matching order: connections → internal accounts → ledgers →
    legal entities → counterparties.  All matchers use list-valued
    lookups for duplicate detection.
    """
    result = ReconciliationResult()
    matched_discovered_ids: set[str] = set()

    # ---------------------------------------------------------------
    # 1. Connections: match entity_id ↔ vendor_id + currency overlap
    # ---------------------------------------------------------------
    vendor_id_to_conns: dict[str, list[DiscoveredConnection]] = {}
    for dc in discovery.connections:
        vendor_id_to_conns.setdefault(dc.vendor_id, []).append(dc)

    # Pre-compute expected currencies per config connection from config IAs
    config_conn_expected_currencies: dict[str, set[str]] = {}
    for ia in config.internal_accounts or []:
        if ia.connection_id.startswith("$ref:"):
            conn_tref = ia.connection_id[5:]
            config_conn_expected_currencies.setdefault(conn_tref, set()).add(
                ia.currency.upper()
            )

    config_conn_to_discovered: dict[str, str] = {}

    all_conn_options = [
        {
            "id": c.id,
            "name": c.vendor_name,
            "detail": ", ".join(c.currencies) or "no IAs",
        }
        for c in discovery.connections
    ]

    for conn in config.connections or []:
        tref = typed_ref_for(conn)
        candidates = vendor_id_to_conns.get(conn.entity_id, [])
        if candidates:
            expected = config_conn_expected_currencies.get(tref, set())
            if len(candidates) > 1 and expected:
                match = max(
                    candidates,
                    key=lambda c: len(set(c.currencies) & expected),
                )
            else:
                match = candidates[0]
            match_reason = f"entity_id={conn.entity_id}"
            if match.currencies:
                match_reason += f", currencies={','.join(match.currencies)}"
            result.matches.append(
                ReconciledResource(
                    config_ref=tref,
                    config_resource=conn,
                    discovered_id=match.id,
                    discovered_name=match.vendor_name,
                    match_reason=match_reason,
                    duplicates=all_conn_options,
                )
            )
            config_conn_to_discovered[tref] = match.id
            matched_discovered_ids.add(match.id)
        else:
            result.unmatched_config.append(tref)

    # ---------------------------------------------------------------
    # 2. Internal accounts: match name + currency + connection
    # ---------------------------------------------------------------
    disc_ia_by_key: dict[tuple[str, str, str], list[DiscoveredInternalAccount]] = {}
    for dia in discovery.internal_accounts:
        key = (
            (dia.name or "").strip().lower(),
            dia.currency.upper(),
            dia.connection_id,
        )
        disc_ia_by_key.setdefault(key, []).append(dia)

    for ia in config.internal_accounts or []:
        tref = typed_ref_for(ia)
        conn_ref_value = ia.connection_id
        resolved_conn_id = ""
        if conn_ref_value.startswith("$ref:"):
            config_conn_ref = conn_ref_value[5:]
            resolved_conn_id = config_conn_to_discovered.get(config_conn_ref, "")
        else:
            resolved_conn_id = conn_ref_value

        key = (ia.name.strip().lower(), ia.currency.upper(), resolved_conn_id)
        candidates = disc_ia_by_key.get(key, [])
        if candidates:
            match = candidates[0]
            dups = None
            if len(candidates) > 1:
                dups = [
                    {
                        "id": c.id,
                        "name": c.name or c.id[:12],
                        "detail": f"{c.currency}, conn={c.connection_ref or c.connection_id[:12]}",
                    }
                    for c in candidates
                ]
            result.matches.append(
                ReconciledResource(
                    config_ref=tref,
                    config_resource=ia,
                    discovered_id=match.id,
                    discovered_name=match.name or match.id[:12],
                    match_reason=f"name+currency+connection ({ia.name}, {ia.currency})",
                    duplicates=dups,
                )
            )
            matched_discovered_ids.add(match.id)
        else:
            result.unmatched_config.append(tref)

    # ---------------------------------------------------------------
    # 3. Ledgers: match by name
    # ---------------------------------------------------------------
    disc_ledger_by_name: dict[str, list[DiscoveredLedger]] = {}
    for dl in discovery.ledgers:
        disc_ledger_by_name.setdefault(dl.name.strip().lower(), []).append(dl)

    for ledger in config.ledgers or []:
        tref = typed_ref_for(ledger)
        candidates = disc_ledger_by_name.get(ledger.name.strip().lower(), [])
        if candidates:
            match = candidates[0]
            dups = None
            if len(candidates) > 1:
                dups = [
                    {"id": c.id, "name": c.name, "detail": ""}
                    for c in candidates
                ]
            result.matches.append(
                ReconciledResource(
                    config_ref=tref,
                    config_resource=ledger,
                    discovered_id=match.id,
                    discovered_name=match.name,
                    match_reason=f"name={ledger.name}",
                    duplicates=dups,
                )
            )
            matched_discovered_ids.add(match.id)
        else:
            result.unmatched_config.append(tref)

    config_ledger_to_discovered: dict[str, str] = {
        m.config_ref: m.discovered_id
        for m in result.matches
        if m.config_ref.startswith("ledger.")
    }

    # ---------------------------------------------------------------
    # 3b. Ledger Accounts: match name + currency + ledger
    # ---------------------------------------------------------------
    disc_la_by_key: dict[tuple[str, str, str], list[DiscoveredLedgerAccount]] = {}
    for dla in discovery.ledger_accounts:
        key = (dla.name.strip().lower(), dla.currency.upper(), dla.ledger_id)
        disc_la_by_key.setdefault(key, []).append(dla)

    for la_cfg in config.ledger_accounts or []:
        tref = typed_ref_for(la_cfg)
        resolved_ledger_id = ""
        if la_cfg.ledger_id.startswith("$ref:"):
            resolved_ledger_id = config_ledger_to_discovered.get(
                la_cfg.ledger_id[5:], ""
            )
        else:
            resolved_ledger_id = la_cfg.ledger_id

        key = (la_cfg.name.strip().lower(), la_cfg.currency.upper(), resolved_ledger_id)
        candidates = disc_la_by_key.get(key, [])
        if candidates:
            match = candidates[0]
            dups = None
            if len(candidates) > 1:
                dups = [
                    {
                        "id": c.id,
                        "name": c.name,
                        "detail": f"{c.currency}, ledger={c.ledger_ref or c.ledger_id[:12]}",
                    }
                    for c in candidates
                ]
            result.matches.append(
                ReconciledResource(
                    config_ref=tref,
                    config_resource=la_cfg,
                    discovered_id=match.id,
                    discovered_name=match.name,
                    match_reason=f"name+currency+ledger ({la_cfg.name}, {la_cfg.currency})",
                    duplicates=dups,
                )
            )
            matched_discovered_ids.add(match.id)
        else:
            result.unmatched_config.append(tref)

    # ---------------------------------------------------------------
    # 3c. Ledger Account Categories: match name + currency + ledger
    # ---------------------------------------------------------------
    disc_lac_by_key: dict[tuple[str, str, str], list[DiscoveredLedgerAccountCategory]] = {}
    for dlac in discovery.ledger_account_categories:
        key = (dlac.name.strip().lower(), dlac.currency.upper(), dlac.ledger_id)
        disc_lac_by_key.setdefault(key, []).append(dlac)

    for lac_cfg in config.ledger_account_categories or []:
        tref = typed_ref_for(lac_cfg)
        resolved_ledger_id = ""
        if lac_cfg.ledger_id.startswith("$ref:"):
            resolved_ledger_id = config_ledger_to_discovered.get(
                lac_cfg.ledger_id[5:], ""
            )
        else:
            resolved_ledger_id = lac_cfg.ledger_id

        key = (lac_cfg.name.strip().lower(), lac_cfg.currency.upper(), resolved_ledger_id)
        candidates = disc_lac_by_key.get(key, [])
        if candidates:
            match = candidates[0]
            dups = None
            if len(candidates) > 1:
                dups = [
                    {
                        "id": c.id,
                        "name": c.name,
                        "detail": f"{c.currency}, ledger={c.ledger_ref or c.ledger_id[:12]}",
                    }
                    for c in candidates
                ]
            result.matches.append(
                ReconciledResource(
                    config_ref=tref,
                    config_resource=lac_cfg,
                    discovered_id=match.id,
                    discovered_name=match.name,
                    match_reason=f"name+currency+ledger ({lac_cfg.name}, {lac_cfg.currency})",
                    duplicates=dups,
                )
            )
            matched_discovered_ids.add(match.id)
        else:
            result.unmatched_config.append(tref)

    # ---------------------------------------------------------------
    # 4. Legal entities: match by type + name
    # ---------------------------------------------------------------
    disc_le_by_key: dict[tuple[str, str], list[DiscoveredLegalEntity]] = {}
    for dle in discovery.legal_entities:
        if dle.legal_entity_type == "business":
            key = ("business", (dle.business_name or "").strip().lower())
        elif dle.legal_entity_type == "individual":
            full = f"{dle.first_name or ''} {dle.last_name or ''}".strip().lower()
            key = ("individual", full)
        elif dle.legal_entity_type == "joint":
            name = (
                dle.business_name
                or f"{dle.first_name or ''} {dle.last_name or ''}".strip()
            ).lower()
            key = ("joint", name)
        else:
            continue
        disc_le_by_key.setdefault(key, []).append(dle)

    for le_cfg in config.legal_entities or []:
        tref = typed_ref_for(le_cfg)
        if le_cfg.legal_entity_type == "business":
            key = ("business", (le_cfg.business_name or "").strip().lower())
        elif le_cfg.legal_entity_type == "individual":
            full = f"{le_cfg.first_name or ''} {le_cfg.last_name or ''}".strip().lower()
            key = ("individual", full)
        else:
            key = ("joint", (le_cfg.business_name or "").strip().lower())

        candidates = disc_le_by_key.get(key, [])
        if candidates:
            match = _pick_best_le(candidates)
            dups = None
            if len(candidates) > 1:
                dups = [
                    {"id": c.id, "name": _le_display_name(c), "detail": f"status={c.status}"}
                    for c in candidates
                ]
            result.matches.append(
                ReconciledResource(
                    config_ref=tref,
                    config_resource=le_cfg,
                    discovered_id=match.id,
                    discovered_name=_le_display_name(match),
                    match_reason=f"type+name ({le_cfg.legal_entity_type})",
                    duplicates=dups,
                )
            )
            matched_discovered_ids.add(match.id)
        else:
            result.unmatched_config.append(tref)

    # ---------------------------------------------------------------
    # 5. Counterparties: match by name
    # ---------------------------------------------------------------
    disc_cp_by_name: dict[str, list[DiscoveredCounterparty]] = {}
    for dcp in discovery.counterparties:
        disc_cp_by_name.setdefault((dcp.name or "").strip().lower(), []).append(dcp)

    for cp_cfg in config.counterparties or []:
        tref = typed_ref_for(cp_cfg)
        candidates = disc_cp_by_name.get(cp_cfg.name.strip().lower(), [])
        if candidates:
            match = candidates[0]
            dups = None
            if len(candidates) > 1:
                dups = [
                    {"id": c.id, "name": c.name, "detail": f"{c.account_count} accounts"}
                    for c in candidates
                ]
            result.matches.append(
                ReconciledResource(
                    config_ref=tref,
                    config_resource=cp_cfg,
                    discovered_id=match.id,
                    discovered_name=match.name,
                    match_reason=f"name={cp_cfg.name}",
                    duplicates=dups,
                )
            )
            matched_discovered_ids.add(match.id)
        else:
            result.unmatched_config.append(tref)

    # ---------------------------------------------------------------
    # Catch-all for reconcilable config resources not yet processed
    # ---------------------------------------------------------------
    reconcilable_types = {
        "connection", "internal_account", "ledger",
        "ledger_account", "ledger_account_category",
        "legal_entity", "counterparty",
    }
    matched_refs = {m.config_ref for m in result.matches}
    unmatched_set = set(result.unmatched_config)
    for res in all_resources(config):
        tref = typed_ref_for(res)
        if res.resource_type not in reconcilable_types:
            continue
        if tref not in matched_refs and tref not in unmatched_set:
            result.unmatched_config.append(tref)
            unmatched_set.add(tref)

    # ---------------------------------------------------------------
    # Unmatched discovered resources
    # ---------------------------------------------------------------
    for dc in discovery.connections:
        if dc.id not in matched_discovered_ids:
            result.unmatched_discovered.append(dc.auto_ref)
    for dia in discovery.internal_accounts:
        if dia.id not in matched_discovered_ids:
            result.unmatched_discovered.append(dia.auto_ref)
    for dl in discovery.ledgers:
        if dl.id not in matched_discovered_ids:
            result.unmatched_discovered.append(dl.auto_ref)
    for dla in discovery.ledger_accounts:
        if dla.id not in matched_discovered_ids:
            result.unmatched_discovered.append(dla.auto_ref)
    for dlac in discovery.ledger_account_categories:
        if dlac.id not in matched_discovered_ids:
            result.unmatched_discovered.append(dlac.auto_ref)
    for dle in discovery.legal_entities:
        if dle.id not in matched_discovered_ids:
            result.unmatched_discovered.append(dle.auto_ref)
    for dcp in discovery.counterparties:
        if dcp.id not in matched_discovered_ids:
            result.unmatched_discovered.append(dcp.auto_ref)

    logger.bind(
        matches=len(result.matches),
        unmatched_config=len(result.unmatched_config),
        unmatched_discovered=len(result.unmatched_discovered),
    ).info("Reconciliation complete")

    return result
