"""Plan 10e — typed context for Fund Flows config drawer (Jinja + optional JSON)."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from dataloader.flow_trace_metadata import step_only_metadata
from dataloader.helpers import get_flow_view_data
from dataloader.routers.flows.flow_list_row import (
    flow_summary_dict_at_index,
    seed_datasets_for_flows_ui,
)


def _working_config_version_token(sess: Any) -> str:
    raw = (
        getattr(sess, "working_config_json", None) or getattr(sess, "config_json_text", None) or ""
    )
    if not isinstance(raw, str) or not raw.strip():
        return "0" * 16
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class FlowConfigDrawerContext(BaseModel):
    """Shared by ``GET …/config-drawer`` (HTML) and ``GET …/config`` (JSON)."""

    model_config = ConfigDict(extra="forbid")

    flow_idx: int
    flow_display_idx: int = Field(description="Same as list / detail row index")
    session_token: str
    pattern_ref: str
    flow_ref: str
    display_title: str | None
    display_summary: str | None
    instance_count: int = 0
    recipe: dict[str, Any] | None = None
    actor_frames: list[dict[str, Any]] = Field(default_factory=list)
    actor_bindings: dict[str, Any] = Field(
        default_factory=dict,
        description="Placeholder until Plan 11a library ids exist",
    )
    timing: dict[str, Any] | None = None
    staging_rules: list[Any] | None = None
    trace_key: str | None = None
    trace_value_resolved: str | None = None
    primary_trace_template: str | None = None
    trace_metadata_extra: dict[str, str] = Field(default_factory=dict)
    per_step_metadata: list[dict[str, Any]] = Field(default_factory=list)
    actor_aliases: list[str] = Field(default_factory=list)
    config_version: str = Field(description="SHA-256 prefix of working JSON (10h prep)")
    seed_datasets: list[Any] = Field(default_factory=list)


def build_flow_config_drawer_context(
    sess: Any, flow_idx: int, session_token: str
) -> dict[str, Any] | None:
    """Return Jinja context + JSON-serializable dict. ``None`` if flow index invalid."""
    summary = flow_summary_dict_at_index(sess, flow_idx)
    if summary is None:
        return None

    result = get_flow_view_data(sess, flow_idx)
    if result is None:
        return None

    flow_ir, flow_config, _view_data = result
    recipe_key = summary["recipe_flow_ref"]
    recipe_raw = None
    if getattr(sess, "generation_recipes", None) and recipe_key in sess.generation_recipes:
        recipe_raw = dict(sess.generation_recipes[recipe_key])

    inst = 0
    if recipe_raw and isinstance(recipe_raw.get("instances"), int):
        inst = recipe_raw["instances"]

    flow_trace_keys = dict(flow_ir.trace_metadata) if flow_ir.trace_metadata else {}
    tk = flow_config.trace_key if flow_config else flow_ir.trace_key
    trace_cfg_md = dict(flow_config.trace_metadata) if flow_config else {}
    primary = trace_cfg_md.get(tk, "{ref}-{instance}") if tk else "{ref}-{instance}"
    trace_extra = {k: str(v) for k, v in trace_cfg_md.items() if k != tk}

    per_step: list[dict[str, Any]] = []
    for step in flow_ir.steps:
        step_meta = step_only_metadata(flow_trace_keys, step.payload)
        per_step.append(
            {
                "step_id": step.step_id,
                "resource_type": step.resource_type,
                "metadata": step_meta,
            }
        )

    aliases = list(flow_config.actors.keys()) if flow_config else []

    timing = None
    staging_rules = None
    if recipe_raw:
        timing = recipe_raw.get("timing")
        staging_rules = recipe_raw.get("staging_rules")

    ctx = FlowConfigDrawerContext(
        flow_idx=flow_idx,
        flow_display_idx=flow_idx,
        session_token=session_token,
        pattern_ref=recipe_key,
        flow_ref=str(summary["flow_ref"]),
        display_title=summary.get("display_title"),
        display_summary=summary.get("display_summary"),
        instance_count=inst,
        recipe=recipe_raw,
        actor_frames=list(summary.get("actor_frames") or []),
        actor_bindings={},
        timing=timing if isinstance(timing, dict) else None,
        staging_rules=staging_rules if isinstance(staging_rules, list) else None,
        trace_key=str(tk) if tk else None,
        trace_value_resolved=str(flow_ir.trace_value) if flow_ir.trace_value is not None else None,
        primary_trace_template=str(primary) if primary is not None else None,
        trace_metadata_extra=trace_extra,
        per_step_metadata=per_step,
        actor_aliases=aliases,
        config_version=_working_config_version_token(sess),
        seed_datasets=seed_datasets_for_flows_ui(),
    )

    base = ctx.model_dump()
    base["flow_summary"] = summary
    base["metadata_key"] = (
        flow_config.view_config.ledger_view.metadata_key
        if flow_config
        and flow_config.view_config
        and flow_config.view_config.ledger_view
        and flow_config.view_config.ledger_view.metadata_key
        else flow_ir.trace_key
    )
    base["metadata_value"] = flow_ir.trace_value
    return base
