"""Fund Flows routes: list, detail, view partials, and generation API."""

from __future__ import annotations

import secrets
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError

from engine import all_resources, dry_run, typed_ref_for
from flow_compiler import (
    GenerationResult,
    actor_display_name,
    compile_diagnostics,
    compute_flow_status,
    flatten_actor_refs,
    flow_account_deltas,
    generate_from_recipe,
)
from flow_views import compute_view_data
from helpers import (
    build_preview,
    fmt_amt,
    format_validation_errors,
    get_flow_view_data,
    get_templates,
    SOURCE_BADGE,
)
from models import DataLoaderConfig, GenerationRecipeV1
from org import reconcile_config
from session import sessions

router = APIRouter(tags=["flows"])

_GEN_SECTIONS = (
    "payment_orders", "incoming_payment_details", "ledger_transactions",
    "expected_payments", "returns", "reversals", "transition_ledger_transactions",
)


def _count_resources(config: DataLoaderConfig) -> dict[str, int]:
    return {s: len(getattr(config, s, None) or []) for s in _GEN_SECTIONS}


async def _parse_and_compile_recipe(
    request: Request,
) -> tuple[str, Any, GenerationRecipeV1, GenerationResult] | JSONResponse:
    """Shared parse -> compile -> reconcile helper for generation endpoints.

    After ``generate_from_recipe`` (post-faker), runs single-pass
    reconciliation against the session's discovery data so that
    ``skip_refs`` and the engine registry are accurate for ``dry_run``.
    """
    token = request.headers.get("x-session-token", "")
    session = sessions.get(token)
    if not session:
        return JSONResponse(
            content={"error": "Session not found. Please validate a config first."},
            status_code=401,
        )

    try:
        body = await request.body()
        recipe = GenerationRecipeV1.model_validate_json(body)
    except ValidationError as e:
        return JSONResponse(
            content={"error": "Invalid recipe", "detail": format_validation_errors(e)},
            status_code=422,
        )

    try:
        base = DataLoaderConfig.model_validate_json(session.config_json_text)
    except ValidationError:
        base = session.config.model_copy(deep=True)
        if session.expanded_flows:
            base.funds_flows = list(session.expanded_flows)

    try:
        gen_result = generate_from_recipe(recipe, base_config=base)
    except (ValueError, KeyError) as e:
        return JSONResponse(
            content={"error": "Generation failed", "detail": str(e)},
            status_code=400,
        )

    if session.discovery is not None:
        reconciliation = reconcile_config(gen_result.config, session.discovery)
        skip_refs: set[str] = set()
        for m in reconciliation.matches:
            if m.use_existing:
                session.registry.register_or_update(m.config_ref, m.discovered_id)
                skip_refs.add(m.config_ref)
        session.reconciliation = reconciliation
        session.skip_refs = skip_refs

    return token, session, recipe, gen_result


@router.get("/flows", include_in_schema=False)
async def flows_page(request: Request):
    """Fund Flows list page — compile-time view of flow patterns."""
    templates = get_templates()
    session_token = request.query_params.get("session_token", "")
    session = sessions.get(session_token)

    if not session:
        return RedirectResponse(url="/setup")

    diagnostics = None
    if session.flow_ir:
        diagnostics = compile_diagnostics(session.flow_ir)

    flow_summaries = []
    orig_flows = session.expanded_flows or []
    if session.flow_ir:
        for i, ir in enumerate(session.flow_ir):
            optional_groups: list[dict] = []
            amount_steps: list[dict] = []
            actors_list: list[dict] = []
            actor_frames: list[dict] = []
            if i < len(orig_flows):
                fc = orig_flows[i]
                for og in fc.optional_groups:
                    optional_groups.append({
                        "label": og.label,
                        "trigger": og.trigger,
                        "step_count": len(og.steps),
                        "step_types": list({s.type for s in og.steps}),
                    })
                for s in fc.steps:
                    amt = getattr(s, "amount", None)
                    if amt is not None:
                        amount_steps.append({
                            "step_id": s.step_id,
                            "type": s.type,
                            "amount": amt,
                        })
                _SLOT_ABBREV = {
                    "counterparty": "CP", "external_account": "EA",
                    "internal_account": "IA", "ledger_account": "LA",
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
                    actors_list.append({
                        "frame_name": frame_name,
                        "alias": frame.alias,
                        "frame_type": frame.frame_type,
                        "customer_name": frame.customer_name or "",
                        "entity_ref": frame.entity_ref or "",
                        "slot_types": sorted(set(slot_abbrevs)),
                    })
                    actor_frames.append({
                        "alias": frame_name,
                        "frame_type": frame.frame_type,
                        "slot_types": sorted(set(slot_full)),
                        "customer_name": frame.customer_name,
                        "entity_ref": frame.entity_ref,
                    })

            og_count = len(optional_groups)
            amounts = [a["amount"] for a in amount_steps]
            amount_range = (
                {"min": min(amounts), "max": max(amounts)}
                if amounts else None
            )

            flow_summaries.append({
                "index": i,
                "flow_ref": ir.flow_ref,
                "pattern_type": ir.pattern_type,
                "trace_key": ir.trace_key,
                "trace_value": ir.trace_value,
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
                    i < len(orig_flows) and orig_flows[i].instance_resources
                ),
            })

    import seed_loader
    seed_datasets = seed_loader.list_datasets()

    return templates.TemplateResponse(
        request,
        "flows.html",
        {
            "session_token": session_token,
            "has_funds_flows": bool(session.flow_ir),
            "flow_summaries": flow_summaries,
            "mermaid_diagrams": session.mermaid_diagrams or [],
            "diagnostics": diagnostics,
            "working_config_json": session.working_config_json or session.config_json_text,
            "generation_recipe": session.generation_recipe,
            "config_json_text": session.config_json_text,
            "seed_datasets": seed_datasets,
        },
    )


@router.get("/flows/view/{flow_idx}", include_in_schema=False)
async def flows_view_page(request: Request, flow_idx: int):
    """Fund Flow detail — view toggle with ledger and payments views."""
    templates = get_templates()
    session_token = request.query_params.get("session_token", "")
    session = sessions.get(session_token)
    if not session:
        return RedirectResponse(url="/setup")

    result = get_flow_view_data(session, flow_idx)
    if result is None:
        return RedirectResponse(url="/setup")

    flow_ir, flow_config, view_data = result

    mermaid_text = None
    if session.mermaid_diagrams and flow_idx < len(session.mermaid_diagrams):
        mermaid_text = session.mermaid_diagrams[flow_idx]

    default_view = view_data.available_views[0] if view_data.available_views else "ledger"

    trace_metadata = dict(flow_ir.trace_metadata) if flow_ir.trace_metadata else {}
    trace_value_template = flow_config.trace_value_template if flow_config else flow_ir.trace_value

    per_step_metadata: list[dict] = []
    for step in flow_ir.steps:
        step_meta = dict(step.trace_metadata) if step.trace_metadata else {}
        per_step_metadata.append({
            "step_id": step.step_id,
            "resource_type": step.resource_type,
            "metadata": step_meta,
        })

    return templates.TemplateResponse(
        request,
        "flows_view.html",
        {
            "session_token": session_token,
            "flow_idx": flow_idx,
            "view_data": view_data,
            "available_views": view_data.available_views,
            "default_view": default_view,
            "mermaid_text": mermaid_text,
            "metadata_key": flow_ir.trace_key,
            "metadata_value": flow_ir.trace_value,
            "trace_value_template": trace_value_template,
            "trace_metadata": trace_metadata,
            "per_step_metadata": per_step_metadata,
            "fmt_amt": fmt_amt,
            "source_badge": SOURCE_BADGE,
        },
    )


@router.get("/api/flows/{flow_idx}/view/ledger", include_in_schema=False)
async def flow_ledger_view_partial(request: Request, flow_idx: int):
    """HTMX partial — ledger view table."""
    templates = get_templates()
    session_token = request.query_params.get("session_token", "")
    session = sessions.get(session_token)
    if not session:
        return HTMLResponse("<p>Session expired</p>", status_code=404)

    result = get_flow_view_data(session, flow_idx)
    if result is None:
        return HTMLResponse("<p>Flow not found</p>", status_code=404)

    _, _, view_data = result
    return templates.TemplateResponse(
        request,
        "partials/flow_ledger_view.html",
        {
            "session_token": session_token,
            "flow_idx": flow_idx,
            "view_data": view_data,
            "fmt_amt": fmt_amt,
            "source_badge": SOURCE_BADGE,
        },
    )


@router.get("/api/flows/{flow_idx}/view/payments", include_in_schema=False)
async def flow_payments_view_partial(request: Request, flow_idx: int):
    """HTMX partial — payments view table."""
    templates = get_templates()
    session_token = request.query_params.get("session_token", "")
    session = sessions.get(session_token)
    if not session:
        return HTMLResponse("<p>Session expired</p>", status_code=404)

    result = get_flow_view_data(session, flow_idx)
    if result is None:
        return HTMLResponse("<p>Flow not found</p>", status_code=404)

    _, _, view_data = result
    return templates.TemplateResponse(
        request,
        "partials/flow_payments_view.html",
        {
            "session_token": session_token,
            "flow_idx": flow_idx,
            "view_data": view_data,
            "fmt_amt": fmt_amt,
            "source_badge": SOURCE_BADGE,
        },
    )


@router.post("/api/flows/{flow_idx}/metadata")
async def update_flow_metadata(request: Request, flow_idx: int):
    """Update trace_key, trace_value_template, trace_metadata, and per-step metadata."""
    token = request.headers.get("x-session-token", "")
    session = sessions.get(token)
    if not session:
        return JSONResponse(
            content={"error": "Session not found"},
            status_code=401,
        )

    body = await request.json()
    trace_key = body.get("trace_key")
    trace_value_template = body.get("trace_value_template")
    trace_metadata = body.get("trace_metadata")
    step_metadata = body.get("step_metadata")

    import json as _json
    try:
        config_dict = _json.loads(session.working_config_json or session.config_json_text)
    except _json.JSONDecodeError:
        config_dict = session.config.model_dump()

    flows = config_dict.get("funds_flows", [])
    if flow_idx < 0 or flow_idx >= len(flows):
        return JSONResponse(content={"error": "Invalid flow index"}, status_code=400)

    flow = flows[flow_idx]
    if trace_key is not None:
        flow["trace_key"] = trace_key
    if trace_value_template is not None:
        flow["trace_value_template"] = trace_value_template
    if trace_metadata is not None:
        flow["trace_metadata"] = trace_metadata

    if step_metadata:
        all_steps = list(flow.get("steps", []))
        for og in flow.get("optional_groups", []):
            all_steps.extend(og.get("steps", []))
        step_by_id = {s.get("step_id"): s for s in all_steps}
        for step_id, meta in step_metadata.items():
            if step_id in step_by_id:
                step_by_id[step_id]["metadata"] = meta

    updated_json = _json.dumps(config_dict, indent=2, ensure_ascii=False)
    session.working_config_json = updated_json

    return {"status": "ok", "flow_idx": flow_idx}


@router.post("/api/flows/generate-preview")
async def generate_preview(request: Request):
    """Return compile stats + Mermaid diagrams without executing."""
    result = await _parse_and_compile_recipe(request)
    if isinstance(result, JSONResponse):
        return result
    session_token, session, recipe, gen = result

    counts = _count_resources(gen.config)
    total = sum(counts.values())
    known = set(session.org_registry.refs.keys()) if session.org_registry else None
    batches = dry_run(gen.config, known, skip_refs=session.skip_refs)
    api_calls = sum(len(b) for b in batches)
    needs_confirmation = api_calls > 10000

    return {
        "counts_by_type": counts,
        "total_resources": total,
        "estimated_batches": len(batches),
        "estimated_api_calls": api_calls,
        "staged_count": recipe.staged_count,
        "mermaid_diagrams": gen.diagrams,
        "edge_case_map": gen.edge_case_map,
        "needs_confirmation": needs_confirmation,
        "confirm_token": secrets.token_urlsafe(16) if needs_confirmation else None,
    }


@router.post("/api/flows/recipe-to-working-config")
async def recipe_to_working_config(request: Request):
    """Compile recipe into a working config and store in session."""
    result = await _parse_and_compile_recipe(request)
    if isinstance(result, JSONResponse):
        return result
    session_token, session, recipe, gen = result

    session.config = gen.config
    config_json_text = gen.config.model_dump_json(indent=2, exclude_none=True)
    session.config_json_text = config_json_text
    session.working_config_json = config_json_text
    session.mermaid_diagrams = gen.diagrams
    session.generation_recipe = recipe.model_dump()
    session.flow_ir = gen.flow_irs
    session.expanded_flows = gen.expanded_flows
    session.view_data_cache = [
        compute_view_data(ir, fc)
        for ir, fc in zip(gen.flow_irs, gen.expanded_flows)
    ]

    known = set(session.org_registry.refs.keys()) if session.org_registry else None
    batches = dry_run(gen.config, known, skip_refs=session.skip_refs)
    session.batches = batches
    resource_map = {typed_ref_for(r): r for r in all_resources(gen.config)}
    session.preview_items = build_preview(batches, resource_map)

    return {
        "status": "ok",
        "total_resources": sum(_count_resources(gen.config).values()),
        "mermaid_count": len(gen.diagrams),
        "edge_case_map": gen.edge_case_map,
    }


@router.post("/api/flows/generate-execute")
async def generate_execute(request: Request):
    """Compile recipe and feed directly into the execution pipeline."""
    result = await _parse_and_compile_recipe(request)
    if isinstance(result, JSONResponse):
        return result
    session_token, session, recipe, gen = result

    session.config = gen.config
    session.mermaid_diagrams = gen.diagrams
    session.generation_recipe = recipe.model_dump()
    session.flow_ir = gen.flow_irs
    session.expanded_flows = gen.expanded_flows
    known = set(session.org_registry.refs.keys()) if session.org_registry else None
    batches = dry_run(gen.config, known, skip_refs=session.skip_refs)
    session.batches = batches

    return {
        "status": "ok",
        "estimated_batches": len(batches),
        "estimated_api_calls": sum(len(b) for b in batches),
        "session_token": session_token,
    }
