"""Immutable execution facts persisted during DAG runs (created / failed / staged)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ManifestEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    batch: int
    resource_type: str
    typed_ref: str
    created_id: str
    created_at: str
    deletable: bool
    child_refs: dict[str, str] = Field(default_factory=dict)
    cleanup_status: str | None = None


class FailedEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    typed_ref: str
    error: str
    failed_at: str
    error_type: str | None = None
    http_status: int | None = None
    error_cause: str | None = None


class StagedEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    resource_type: str
    typed_ref: str
    staged_at: str
