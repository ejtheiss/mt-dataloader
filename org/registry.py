"""Pydantic-backed org registry mapping discovered typed_refs to live UUIDs.

Replaces the old ``seed_registry`` + ``baseline_from_discovery`` two-step.
Built from ``DiscoveryResult``; serializable, validatable, frozen after
construction.  The engine's mutable ``RefRegistry`` is seeded from this
at execute time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from dataloader.engine import RefRegistry

from .discovery import DiscoveryResult

__all__ = ["OrgRegistry"]


class OrgRegistry(BaseModel):
    """Frozen Pydantic model mapping discovered typed_refs to live UUIDs.

    Stored on the session as the single source of truth for what exists
    in the MT org.  The engine's ``RefRegistry`` is seeded from this
    before dry_run or execute.
    """

    model_config = ConfigDict(frozen=True)

    refs: dict[str, str] = Field(
        default_factory=dict,
        description="typed_ref -> UUID for all discovered org resources",
    )

    @classmethod
    def from_discovery(cls, discovery: DiscoveryResult) -> OrgRegistry:
        """Build an OrgRegistry from a DiscoveryResult."""
        refs: dict[str, str] = {}
        for c in discovery.connections:
            refs[c.auto_ref] = c.id
        for ia in discovery.internal_accounts:
            refs[ia.auto_ref] = ia.id
        for lg in discovery.ledgers:
            refs[lg.auto_ref] = lg.id
        for la in discovery.ledger_accounts:
            refs[la.auto_ref] = la.id
        for lac in discovery.ledger_account_categories:
            refs[lac.auto_ref] = lac.id
        for le in discovery.legal_entities:
            refs[le.auto_ref] = le.id
        for cp in discovery.counterparties:
            refs[cp.auto_ref] = cp.id
        return cls(refs=refs)

    def seed_engine_registry(self, engine_registry: RefRegistry) -> set[str]:
        """Populate the engine's mutable RefRegistry.

        Returns the set of seeded typed refs (for use as ``known_refs``
        in ``dry_run``).
        """
        for ref, uuid in self.refs.items():
            engine_registry.register_or_update(ref, uuid)
        return set(self.refs.keys())
