"""Shared types for org reconciliation (no matcher logic)."""

from __future__ import annotations

from dataclasses import dataclass, field

from models import _BaseResourceConfig


@dataclass
class ReconciledResource:
    config_ref: str
    config_resource: _BaseResourceConfig
    discovered_id: str
    discovered_name: str
    match_reason: str
    use_existing: bool = True
    duplicates: list[dict] | None = None
    child_refs: dict[str, str] = field(default_factory=dict)


@dataclass
class ReconciliationResult:
    matches: list[ReconciledResource] = field(default_factory=list)
    unmatched_config: list[str] = field(default_factory=list)
    unmatched_discovered: list[str] = field(default_factory=list)
