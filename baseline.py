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

from engine import RefRegistry

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
    "DiscoveryResult",
    "discover_org",
    "baseline_from_discovery",
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
        name_getter=lambda live: live.business_name or "(no business_name)",
        entry_name_getter=lambda e: e.business_name,
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
class DiscoveryResult:
    connections: list[DiscoveredConnection] = field(default_factory=list)
    internal_accounts: list[DiscoveredInternalAccount] = field(default_factory=list)
    ledgers: list[DiscoveredLedger] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


async def discover_org(client: AsyncModernTreasury) -> DiscoveryResult:
    """Discover connections, internal accounts, and ledgers from a live org.

    Collects all resources of each type first, then batch-assigns deterministic
    refs.  Returns a ``DiscoveryResult`` ready for ``baseline_from_discovery()``.
    """
    result = DiscoveryResult()

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

    # --- Build connection ID → ref lookup for IA cross-references ---
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

    logger.bind(
        connections=len(result.connections),
        internal_accounts=len(result.internal_accounts),
        ledgers=len(result.ledgers),
        warnings=len(result.warnings),
    ).info("Org discovery complete")

    return result


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

    return BaselineConfig(
        connections=connections,
        internal_accounts=internal_accounts,
        ledgers=ledgers,
    )
