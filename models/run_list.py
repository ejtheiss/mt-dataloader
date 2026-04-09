"""Slim row shape for GET /api/runs (Wave B — SQL-backed list, no full manifest parse)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from models.manifest import RunManifest


class RunListRow(BaseModel):
    """Fields required by ``templates/partials/runs_list_body.html`` (resource/staged/failed counts)."""

    model_config = ConfigDict(extra="ignore")

    run_id: str
    status: str
    started_at: str
    resource_count: int = Field(ge=0)
    staged_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    mt_org_id: str | None = None

    @classmethod
    def from_manifest(cls, m: RunManifest) -> RunListRow:
        return cls(
            run_id=m.run_id,
            status=str(m.status),
            started_at=m.started_at,
            resource_count=len(m.resources_created),
            staged_count=len(m.resources_staged) if m.resources_staged else 0,
            failed_count=len(m.resources_failed) if m.resources_failed else 0,
            mt_org_id=m.mt_org_id,
        )
