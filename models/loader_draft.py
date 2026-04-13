"""Durable loader continuity payload (Wave D) — JSON-only; no secrets or compiled Flow IR.

Rebuild at runtime (not stored): ``registry`` (``RefRegistry``), ``org_registry``,
``discovery``, ``reconciliation``, ``flow_ir``, ``expanded_flows``,
``pattern_flow_ir``, ``pattern_expanded_flows``, ``view_data_cache``,
``cleanup_resources`` / ``cleanup_run_id`` (ephemeral cleanup SSE only), in-memory ``config`` (parse from ``config_json_text``),
``api_key`` / ``session_token`` (ephemeral; re-prompt per Plan 0).

**SessionState fields → LoaderDraft (persist)**

- **Stored:** ``org_id``, ``org_label``, ``config_json_text`` (must parse as
  ``DataLoaderConfig``), ``batches``, ``preview_items`` (no secrets — caller
  responsibility), optional ``base_config_json`` / ``authoring_config_json`` /
  ``working_config_json`` (non-empty ⇒ must parse as ``DataLoaderConfig``),
  ``generation_recipes``, ``mermaid_diagrams``, ``source_file_path``,
  ``flow_diagnostics``, ``skip_refs``, ``update_refs``, ``payload_overrides``.
- **Not stored:** ``api_key``, ``session_token``, ``registry``, ``org_registry``,
  ``discovery``, ``reconciliation``, ``flow_ir``, ``expanded_flows``,
  ``pattern_*``, ``view_data_cache``, ``cleanup_resources``, ``created_at`` (use
  row ``updated_at`` / TTL policy instead).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from models.actor_library import LibraryActorEntry
from models.config import DataLoaderConfig


class LoaderDraft(BaseModel):
    """Versioned JSON document stored in ``loader_drafts.draft_json``."""

    schema_version: Literal[1] = 1

    org_id: str = ""
    org_label: str | None = None
    config_json_text: str = "{}"
    batches: list[list[str]] = Field(default_factory=list)
    preview_items: list[dict[str, Any]] = Field(default_factory=list)
    base_config_json: str | None = None
    authoring_config_json: str | None = None
    working_config_json: str | None = None
    generation_recipes: dict[str, dict[str, Any]] = Field(default_factory=dict)
    mermaid_diagrams: list[str] = Field(default_factory=list)
    source_file_path: str | None = None
    flow_diagnostics: list[dict[str, Any]] | None = None
    skip_refs: list[str] = Field(default_factory=list)
    update_refs: dict[str, str] = Field(default_factory=dict)
    payload_overrides: list[str] = Field(default_factory=list)
    #: Plan 11a — shared actor definitions (session-scoped, draft-persisted).
    actor_library: list[LibraryActorEntry] = Field(default_factory=list)
    #: recipe_flow_ref (pattern key) → frame_name → library_actor_id
    actor_bindings: dict[str, dict[str, str]] = Field(default_factory=dict)

    @field_validator("config_json_text")
    @classmethod
    def _config_json_text_is_dataloader(cls, v: str) -> str:
        DataLoaderConfig.model_validate_json(v)
        return v

    @field_validator("base_config_json", "authoring_config_json", "working_config_json")
    @classmethod
    def _optional_config_json_is_dataloader(cls, v: str | None) -> str | None:
        if v is not None and v.strip():
            DataLoaderConfig.model_validate_json(v)
        return v
