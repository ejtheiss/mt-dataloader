"""Mutable execution state during DAG runs (engine-internal; not ``RunManifest``).

``RunManifest`` remains for JSON round-trip, backfill, and tests. The execute
path uses this accumulator so callers receive only ``ExecutionResultSummary``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from models.manifest import FailedEntry, ManifestEntry, StagedEntry

from .run_meta import _now_iso


@dataclass
class ExecutionAccumulator:
    """In-memory facts collected during ``execute()`` before persistence finalization."""

    run_id: str
    config_hash: str
    mt_org_id: str | None = None
    mt_org_label: str | None = None
    started_at: str = field(default_factory=_now_iso)
    completed_at: str | None = None
    status: str = "running"
    resources_created: list[ManifestEntry] = field(default_factory=list)
    resources_failed: list[FailedEntry] = field(default_factory=list)
    resources_staged: list[StagedEntry] = field(default_factory=list)

    def record(self, entry: ManifestEntry) -> None:
        self.resources_created.append(entry)

    def record_failure(
        self,
        typed_ref: str,
        error: str,
        *,
        error_type: str | None = None,
        http_status: int | None = None,
        error_cause: str | None = None,
    ) -> None:
        self.resources_failed.append(
            FailedEntry(
                typed_ref=typed_ref,
                error=error,
                failed_at=_now_iso(),
                error_type=error_type,
                http_status=http_status,
                error_cause=error_cause,
            )
        )

    def record_staged(self, typed_ref: str, resource_type: str) -> None:
        self.resources_staged.append(
            StagedEntry(
                resource_type=resource_type,
                typed_ref=typed_ref,
                staged_at=_now_iso(),
            )
        )

    def finalize(self, status: str) -> None:
        self.status = status
        self.completed_at = _now_iso()
