"""Immutable summary returned from ``engine.execute``."""

from __future__ import annotations

from dataclasses import dataclass

from models.run_views import RunExecuteSummaryDTO


@dataclass(frozen=True)
class ExecutionResultSummary:
    run_id: str
    status: str
    completed_at: str | None
    resources_created_count: int
    resources_staged_count: int
    resources_failed_count: int

    def to_execute_summary_dto(self) -> RunExecuteSummaryDTO:
        return RunExecuteSummaryDTO(
            run_id=self.run_id,
            created_count=self.resources_created_count,
            staged_count=self.resources_staged_count,
            failed_count=self.resources_failed_count,
            has_staged=self.resources_staged_count > 0,
        )
