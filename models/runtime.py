"""Internal runtime types — handler results and manifest entries.

These are NOT user-facing; they are frozen dataclasses used by the
engine and cleanup layers.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class HandlerResult:
    """Returned by every handler after a successful SDK create call."""

    created_id: str
    resource_type: str
    typed_ref: str = ""
    child_refs: dict[str, str] = field(default_factory=dict)
    raw_response: dict | None = None
    deletable: bool = True


@dataclass(frozen=True)
class ManifestEntry:
    """Single resource entry in a run manifest."""

    batch: int
    resource_type: str
    typed_ref: str
    created_id: str
    created_at: str
    deletable: bool
    child_refs: dict[str, str] = field(default_factory=dict)
    cleanup_status: str | None = None


@dataclass(frozen=True)
class FailedEntry:
    """Single failed resource entry in a run manifest."""

    typed_ref: str
    error: str
    failed_at: str


@dataclass(frozen=True)
class StagedEntry:
    """Resource resolved but not sent to API — staged for manual fire during demo."""

    resource_type: str
    typed_ref: str
    staged_at: str
