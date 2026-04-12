"""Fund Flows HTML pages: list and detail."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

import flow_compiler.seed_loader as seed_loader
from dataloader.flow_trace_metadata import step_only_metadata
from dataloader.helpers import fmt_amt, get_flow_view_data
from dataloader.routers.deps import OptionalSessionQueryDep, TemplatesDep
from dataloader.routers.flows.flow_list_row import flow_summary_dict_at_index
from dataloader.routers.flows.helpers import _display_flow_session_sources
from flow_compiler import compile_diagnostics
from models import SOURCE_BADGE

router = APIRouter()


def _flow_view_tabs(
    *, flow_idx: int, session_token: str, available_views: list[str]
) -> list[dict[str, Any]]:
    """Build ``tabs`` for ``partials/mt_tabs.html`` (Plan 10a — single source for tab UI)."""
    return [
        {
            "id": v,
            "label": str(v).capitalize(),
            "url": f"/api/flows/{flow_idx}/view/{v}?session_token={session_token}",
        }
        for v in available_views
    ]


@router.get("/flows", include_in_schema=False)
async def flows_page(
    request: Request,
    templates: TemplatesDep,
    sess: OptionalSessionQueryDep,
):
    """Fund Flows list page — compile-time view of flow patterns."""
    if not sess:
        return RedirectResponse(url="/setup")

    session_token = sess.session_token

    display_flow_ir, display_expanded = _display_flow_session_sources(sess)

    diagnostics = None
    if display_flow_ir:
        diagnostics = compile_diagnostics(display_flow_ir)

    flow_summaries = []
    if display_flow_ir:
        for i in range(len(display_flow_ir)):
            row = flow_summary_dict_at_index(sess, i)
            if row:
                flow_summaries.append(row)

    seed_datasets = seed_loader.list_datasets()

    return templates.TemplateResponse(
        request,
        "flows.html",
        {
            "session_token": session_token,
            "has_funds_flows": bool(display_flow_ir),
            "flow_summaries": flow_summaries,
            "mermaid_diagrams": sess.mermaid_diagrams or [],
            "diagnostics": diagnostics,
            "working_config_json": sess.working_config_json or sess.config_json_text,
            "generation_recipes": sess.generation_recipes,
            "config_json_text": sess.config_json_text,
            "seed_datasets": seed_datasets,
        },
    )


@router.get("/flows/view/{flow_idx}", include_in_schema=False)
async def flows_view_page(
    request: Request,
    flow_idx: int,
    templates: TemplatesDep,
    sess: OptionalSessionQueryDep,
):
    """Fund Flow detail — view toggle with ledger and payments views."""
    if not sess:
        return RedirectResponse(url="/setup")

    session_token = sess.session_token

    result = get_flow_view_data(sess, flow_idx)
    if result is None:
        return RedirectResponse(url="/setup")

    flow_ir, flow_config, view_data = result

    mermaid_text = None
    if sess.mermaid_diagrams and flow_idx < len(sess.mermaid_diagrams):
        mermaid_text = sess.mermaid_diagrams[flow_idx]

    default_view = view_data.available_views[0] if view_data.available_views else "ledger"

    flow_trace_keys = dict(flow_ir.trace_metadata) if flow_ir.trace_metadata else {}
    tk = flow_config.trace_key if flow_config else flow_ir.trace_key
    trace_cfg_md = dict(flow_config.trace_metadata) if flow_config else {}
    primary_trace_template = trace_cfg_md.get(tk, "{ref}-{instance}")
    trace_metadata_extra = {k: v for k, v in trace_cfg_md.items() if k != tk}

    per_step_metadata: list[dict] = []
    for step in flow_ir.steps:
        step_meta = step_only_metadata(flow_trace_keys, step.payload)
        per_step_metadata.append(
            {
                "step_id": step.step_id,
                "resource_type": step.resource_type,
                "metadata": step_meta,
            }
        )

    actor_aliases = list(flow_config.actors.keys()) if flow_config else []

    tabs = _flow_view_tabs(
        flow_idx=flow_idx,
        session_token=session_token,
        available_views=list(view_data.available_views),
    )

    return templates.TemplateResponse(
        request,
        "flows_view.html",
        {
            "session_token": session_token,
            "flow_idx": flow_idx,
            "view_data": view_data,
            "available_views": view_data.available_views,
            "tabs": tabs,
            "active_tab": default_view,
            "tab_target": "#view-content",
            "default_view": default_view,
            "mermaid_text": mermaid_text,
            "metadata_key": (
                flow_config.view_config.ledger_view.metadata_key
                if flow_config
                and flow_config.view_config
                and flow_config.view_config.ledger_view
                and flow_config.view_config.ledger_view.metadata_key
                else flow_ir.trace_key
            ),
            "metadata_value": flow_ir.trace_value,
            "primary_trace_template": primary_trace_template,
            "trace_metadata_extra": trace_metadata_extra,
            "per_step_metadata": per_step_metadata,
            "actor_aliases": actor_aliases,
            "fmt_amt": fmt_amt,
            "source_badge": SOURCE_BADGE,
        },
    )
