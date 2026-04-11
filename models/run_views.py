"""Immutable view DTOs for run detail, cleanup, SSE (no ORM above repositories)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreatedResourceRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    batch: int
    resource_type: str
    typed_ref: str
    created_id: str
    created_at: str
    deletable: bool
    child_refs: dict[str, str] = Field(default_factory=dict)
    cleanup_status: str | None = None
    display_name: str | None = None
    deps: list[str] | None = None
    sandbox_info: str | None = None
    payload: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class FailedResourceRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    typed_ref: str
    error: str
    failed_at: str
    error_type: str | None = None
    http_status: int | None = None
    error_cause: str | None = None


class StagedItemView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    resource_type: str
    typed_ref: str
    staged_at: str


class RunDetailView(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    run_id: str
    status: str
    started_at: str
    completed_at: str | None
    config_hash: str | None
    mt_org_id: str | None
    mt_org_label: str | None
    resources_created: tuple[CreatedResourceRow, ...]
    resources_failed: tuple[FailedResourceRow, ...]
    resources_staged: tuple[StagedItemView, ...]
    config_json: str
    staged_payloads: dict[str, dict[str, Any]]


class RunExecuteSummaryDTO(BaseModel):
    """SSE ``run_complete`` partial — counts + flags only."""

    model_config = ConfigDict(frozen=True)

    run_id: str
    created_count: int
    staged_count: int
    failed_count: int
    has_staged: bool
