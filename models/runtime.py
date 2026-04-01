"""Internal runtime types — handler results.

Manifest entries live in ``models.manifest`` (Pydantic) for JSON round-trip.
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
