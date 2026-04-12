"""Single-row summary for Fund Flows list / config drawer (shared with ``page.py``)."""

from __future__ import annotations

from typing import Any

import flow_compiler.seed_loader as seed_loader
from dataloader.routers.flows.helpers import (
    _display_flow_session_sources,
    _recipe_flow_ref,
    _step_variance_ui_fields,
    get_funds_flow_display_fields_for_display_row,
)
from flow_compiler import compute_flow_status, flow_account_deltas


def flow_summary_dict_at_index(sess: Any, i: int) -> dict[str, Any] | None:
    """Build one ``flow_summaries`` entry (same keys as ``flows_page``)."""
    display_flow_ir, display_expanded = _display_flow_session_sources(sess)
    if i < 0 or i >= len(display_flow_ir):
        return None
    ir = display_flow_ir[i]

    optional_groups: list[dict] = []
    amount_steps: list[dict] = []
    actors_list: list[dict] = []
    actor_frames: list[dict] = []
    recipe_key = _recipe_flow_ref(ir.flow_ref)
    recipe_for_flow: dict[str, Any] | None = None
    if sess.generation_recipes and recipe_key in sess.generation_recipes:
        recipe_for_flow = sess.generation_recipes[recipe_key]

    if i < len(display_expanded):
        fc = display_expanded[i]
        for og in fc.optional_groups:
            optional_groups.append(
                {
                    "label": og.label,
                    "trigger": og.trigger,
                    "step_count": len(og.steps),
                    "step_types": list({s.type for s in og.steps}),
                }
            )
        for s in fc.steps:
            amt = getattr(s, "amount", None)
            if amt is not None:
                row = {
                    "step_id": s.step_id,
                    "type": s.type,
                    "amount": amt,
                }
                row.update(_step_variance_ui_fields(s.step_id, recipe_for_flow))
                amount_steps.append(row)
        _SLOT_ABBREV = {
            "counterparty": "CP",
            "external_account": "EA",
            "internal_account": "IA",
            "ledger_account": "LA",
            "virtual_account": "VA",
        }
        for frame_name, frame in fc.actors.items():
            slot_abbrevs: list[str] = []
            slot_full: list[str] = []
            for _sn, slot in frame.slots.items():
                ref = slot.ref if hasattr(slot, "ref") else slot
                if "$ref:" in ref:
                    st = ref.replace("$ref:", "").split(".")[0]
                    slot_abbrevs.append(_SLOT_ABBREV.get(st, st))
                    slot_full.append(st)
            actors_list.append(
                {
                    "frame_name": frame_name,
                    "alias": frame.alias,
                    "frame_type": frame.frame_type,
                    "customer_name": frame.customer_name or "",
                    "entity_ref": frame.entity_ref or "",
                    "slot_types": sorted(set(slot_abbrevs)),
                }
            )
            actor_frames.append(
                {
                    "alias": frame_name,
                    "frame_type": frame.frame_type,
                    "slot_types": sorted(set(slot_full)),
                    "customer_name": frame.customer_name,
                    "entity_ref": frame.entity_ref,
                }
            )

    og_count = len(optional_groups)
    amounts = [a["amount"] for a in amount_steps]
    amount_range = {"min": min(amounts), "max": max(amounts)} if amounts else None

    fc_meta = display_expanded[i] if i < len(display_expanded) else None
    display_title, display_summary = get_funds_flow_display_fields_for_display_row(sess, i, fc_meta)

    return {
        "index": i,
        "flow_ref": ir.flow_ref,
        "recipe_flow_ref": recipe_key,
        "pattern_type": ir.pattern_type,
        "trace_key": ir.trace_key,
        "trace_value": ir.trace_value,
        "display_title": display_title,
        "display_summary": display_summary,
        "step_count": len(ir.steps),
        "og_count": og_count,
        "amount_range": amount_range,
        "status": compute_flow_status(ir),
        "account_deltas": flow_account_deltas(ir),
        "optional_groups": optional_groups,
        "amount_steps": amount_steps,
        "actors": actors_list,
        "actor_frames": actor_frames,
        "has_instance_resources": bool(
            i < len(display_expanded) and display_expanded[i].instance_resources
        ),
    }


def seed_datasets_for_flows_ui() -> list:
    """Lazy import wrapper (same as ``flows_page``)."""
    return seed_loader.list_datasets()
