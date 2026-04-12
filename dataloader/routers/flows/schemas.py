"""Shared Pydantic request bodies for Fund Flows API routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from models import ActorDatasetOverride


class ScenarioSnapshotRequest(BaseModel):
    """Optional body for ``POST /api/flows/scenario-snapshot``."""

    flow_ref: str | None = None


class RecipePatchBody(BaseModel):
    """Merge ``patch`` into the stored recipe for ``flow_ref``, validate, recompose (plan 05)."""

    flow_ref: str = Field(..., min_length=1)
    patch: dict[str, Any] = Field(default_factory=dict)


class ActorConfigSaveBody(BaseModel):
    model_config = {"extra": "forbid"}

    frame: str = Field(min_length=1)
    override: ActorDatasetOverride = Field(default_factory=ActorDatasetOverride)
