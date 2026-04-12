"""Fund Flows JSON / mutation API routes."""

from __future__ import annotations

import json
import secrets

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from dataloader.engine import dry_run
from dataloader.flow_trace_metadata import forbidden_trace_keys
from dataloader.flows_mutation import (
    compose_all_recipes,
    default_recipe_dict,
    get_base_config_for_generation,
    merge_recipe_dict,
    recompose_and_persist_session,
)
from dataloader.helpers import format_validation_errors
from dataloader.routers.deps import OptionalSessionQueryDep, SessionHeaderDep
from dataloader.routers.flows.helpers import (
    _count_resources,
    _parse_and_compile_recipe,
    _parse_recipe,
    resolve_working_funds_flow_index_for_metadata,
)
from dataloader.routers.flows.schemas import RecipePatchBody, ScenarioSnapshotRequest
from dataloader.session import sessions
from dataloader.session.draft_persist import persist_loader_draft
from dataloader.view_models.flows_config_drawer import (
    FlowConfigDrawerContext,
    build_flow_config_drawer_context,
)
from jsonutil import dumps_pretty, loads_str
from models import GenerationRecipeV1

router = APIRouter()


@router.get("/api/flows/{flow_idx}/config", tags=["agent"])
async def flow_config_drawer_json(flow_idx: int, sess: OptionalSessionQueryDep):
    """JSON twin of ``GET …/config-drawer`` (Plan 10e — machine clients)."""
    if not sess:
        return JSONResponse(content={"error": "Session not found"}, status_code=401)
    ctx = build_flow_config_drawer_context(sess, flow_idx, sess.session_token)
    if ctx is None:
        return JSONResponse(content={"error": "Flow not found"}, status_code=404)
    keys = set(FlowConfigDrawerContext.model_fields.keys())
    payload = {k: ctx[k] for k in keys if k in ctx}
    model = FlowConfigDrawerContext.model_validate(payload)
    return JSONResponse(content=model.model_dump(mode="json"))


@router.post("/api/flows/{flow_idx}/metadata", tags=["agent"])
async def update_flow_metadata(
    request: Request,
    flow_idx: int,
    hdr_sess: SessionHeaderDep,
):
    """Update trace_key, trace_metadata (incl. primary template at trace_key), per-step metadata."""
    if not hdr_sess:
        return JSONResponse(
            content={"error": "Session not found"},
            status_code=401,
        )

    body = await request.json()
    trace_key = body.get("trace_key")
    trace_metadata = body.get("trace_metadata")
    legacy_trace_tpl = body.get("trace_value_template")
    step_metadata = body.get("step_metadata")

    try:
        config_dict = loads_str(hdr_sess.working_config_json or hdr_sess.config_json_text)
    except json.JSONDecodeError:
        config_dict = hdr_sess.config.model_dump()

    flows = config_dict.get("funds_flows", [])
    try:
        edit_idx = resolve_working_funds_flow_index_for_metadata(hdr_sess, flow_idx)
    except ValueError as exc:
        return JSONResponse(
            content={"error": "Invalid flow index", "detail": str(exc)},
            status_code=400,
        )

    if edit_idx < 0 or edit_idx >= len(flows):
        return JSONResponse(content={"error": "Invalid flow index"}, status_code=400)

    flow = flows[edit_idx]
    if trace_key is not None:
        flow["trace_key"] = trace_key
    if trace_metadata is not None:
        flow["trace_metadata"] = trace_metadata
    if legacy_trace_tpl is not None:
        eff_tk0 = trace_key if trace_key is not None else flow.get("trace_key", "deal_id")
        tm0 = dict(flow.get("trace_metadata") or {})
        if eff_tk0 not in tm0 or not str(tm0.get(eff_tk0, "")).strip():
            tm0[eff_tk0] = legacy_trace_tpl
            flow["trace_metadata"] = tm0

    eff_tk = trace_key if trace_key is not None else flow.get("trace_key")
    eff_tm: dict[str, str] | None
    if trace_metadata is not None:
        eff_tm = dict(trace_metadata)
    else:
        raw_tm = flow.get("trace_metadata")
        eff_tm = dict(raw_tm) if isinstance(raw_tm, dict) else {}
    forbidden = forbidden_trace_keys(
        eff_tk if isinstance(eff_tk, str) else None,
        eff_tm,
    )

    if step_metadata:
        all_steps = list(flow.get("steps", []))
        for og in flow.get("optional_groups", []):
            all_steps.extend(og.get("steps", []))
        step_by_id = {s.get("step_id"): s for s in all_steps}
        for step_id, meta in step_metadata.items():
            if step_id in step_by_id:
                cleaned = {
                    k: v
                    for k, v in meta.items()
                    if k not in forbidden and not str(k).startswith("_flow_")
                }
                existing = step_by_id[step_id].get("metadata") or {}
                internal = {k: v for k, v in existing.items() if str(k).startswith("_flow_")}
                step_by_id[step_id]["metadata"] = {**internal, **cleaned}

    if "display_title" in body:
        raw_dt = body["display_title"]
        if raw_dt is None:
            flow["display_title"] = None
        elif isinstance(raw_dt, str):
            s = raw_dt.strip()
            if len(s) > 120:
                return JSONResponse(
                    content={"error": "display_title exceeds 120 characters"},
                    status_code=422,
                )
            flow["display_title"] = s or None
        else:
            return JSONResponse(
                content={"error": "display_title must be a string or null"},
                status_code=422,
            )

    if "display_summary" in body:
        raw_ds = body["display_summary"]
        if raw_ds is None:
            flow["display_summary"] = None
        elif isinstance(raw_ds, str):
            s2 = raw_ds.strip()
            if len(s2) > 500:
                return JSONResponse(
                    content={"error": "display_summary exceeds 500 characters"},
                    status_code=422,
                )
            flow["display_summary"] = s2 or None
        else:
            return JSONResponse(
                content={"error": "display_summary must be a string or null"},
                status_code=422,
            )

    updated_json = dumps_pretty(config_dict)
    hdr_sess.working_config_json = updated_json

    await persist_loader_draft(request, hdr_sess)

    return {
        "status": "ok",
        "flow_idx": flow_idx,
        "edit_index": edit_idx,
        "display_title": flow.get("display_title"),
        "display_summary": flow.get("display_summary"),
    }


@router.post("/api/flows/generate-preview", tags=["agent"])
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

    await persist_loader_draft(request, session)

    return {
        "counts_by_type": counts,
        "total_resources": total,
        "estimated_batches": len(batches),
        "estimated_api_calls": api_calls,
        "staged_count": sum(r.count for r in recipe.staging_rules),
        "mermaid_diagrams": gen.diagrams,
        "edge_case_map": gen.edge_case_map,
        "needs_confirmation": needs_confirmation,
        "confirm_token": secrets.token_urlsafe(16) if needs_confirmation else None,
    }


@router.post("/api/flows/recipe-to-working-config", tags=["agent"])
async def recipe_to_working_config(request: Request):
    """Store a full recipe body for one flow, then compose all stored recipes.

    Prefer ``POST /api/flows/recipe-patch`` for UI/agents that merge into an existing recipe.
    """
    result = await _parse_recipe(request)
    if isinstance(result, JSONResponse):
        return result
    session_token, session, recipe = result

    session.generation_recipes[recipe.flow_ref] = recipe.model_dump()

    composed = await recompose_and_persist_session(request, session)
    if isinstance(composed, JSONResponse):
        return composed

    recipe_count = len(session.generation_recipes)
    return {
        "status": "ok",
        "total_resources": sum(_count_resources(composed.config).values()),
        "mermaid_count": len(composed.diagrams),
        "edge_case_map": composed.edge_case_map,
        "recipe_count": recipe_count,
    }


@router.post("/api/flows/scenario-snapshot", tags=["agent"])
async def scenario_snapshot(request: Request):
    """Return server-side ``generation_recipes`` (plan 05 — client sync / hydration).

    Body: optional JSON ``{\"flow_ref\": \"...\"}``. When ``flow_ref`` is set, returns that
    recipe plus ``default_recipe`` for new authoring; otherwise returns all recipes.
    """
    token = request.headers.get("x-session-token", "")
    session = sessions.get(token)
    if not session:
        return JSONResponse(
            content={"error": "Session not found. Please validate a config first."},
            status_code=401,
        )
    raw = await request.body()
    if not raw.strip():
        req = ScenarioSnapshotRequest()
    else:
        try:
            req = ScenarioSnapshotRequest.model_validate_json(raw)
        except ValidationError as exc:
            return JSONResponse(
                content={"error": "Invalid body", "detail": format_validation_errors(exc)},
                status_code=422,
            )
    recipes = session.generation_recipes
    if req.flow_ref:
        stored = recipes.get(req.flow_ref)
        return {
            "flow_ref": req.flow_ref,
            "recipe": stored,
            "has_recipe": stored is not None,
            "default_recipe": default_recipe_dict(req.flow_ref),
        }
    return {
        "recipes": dict(recipes),
        "flow_refs": list(recipes.keys()),
    }


@router.post("/api/flows/recipe-patch", tags=["agent"])
async def recipe_patch(request: Request):
    """Merge a partial (or full) recipe dict into the stored recipe, validate, recompose (plan 05).

    Scenario builder **Apply** sends ``{ flow_ref, patch: <full buildRecipe()> }``. Small patches
    suit tools/agents; a complete recipe body with no merge can use
    ``POST /api/flows/recipe-to-working-config``.
    """
    token = request.headers.get("x-session-token", "")
    session = sessions.get(token)
    if not session:
        return JSONResponse(
            content={"error": "Session not found. Please validate a config first."},
            status_code=401,
        )
    raw = await request.body()
    try:
        body = RecipePatchBody.model_validate_json(raw)
    except ValidationError as exc:
        return JSONResponse(
            content={"error": "Invalid body", "detail": format_validation_errors(exc)},
            status_code=422,
        )
    base = session.generation_recipes.get(body.flow_ref) or default_recipe_dict(body.flow_ref)
    if base.get("flow_ref") != body.flow_ref:
        base = {**base, "flow_ref": body.flow_ref}
    merged = merge_recipe_dict(base, body.patch)
    try:
        recipe = GenerationRecipeV1.model_validate(merged)
    except ValidationError as exc:
        return JSONResponse(
            content={"error": "Invalid merged recipe", "detail": format_validation_errors(exc)},
            status_code=422,
        )
    session.generation_recipes[recipe.flow_ref] = recipe.model_dump()

    composed = await recompose_and_persist_session(request, session)
    if isinstance(composed, JSONResponse):
        return composed

    recipe_count = len(session.generation_recipes)
    return {
        "status": "ok",
        "total_resources": sum(_count_resources(composed.config).values()),
        "mermaid_count": len(composed.diagrams),
        "edge_case_map": composed.edge_case_map,
        "recipe_count": recipe_count,
        "recipe": recipe.model_dump(),
    }


@router.post("/api/flows/generate-execute", tags=["agent"])
async def generate_execute(request: Request):
    """Compile recipe and feed directly into the execution pipeline."""
    result = await _parse_recipe(request)
    if isinstance(result, JSONResponse):
        return result
    session_token, session, recipe = result

    session.generation_recipes[recipe.flow_ref] = recipe.model_dump()

    base = get_base_config_for_generation(session)
    try:
        gen = compose_all_recipes(base, session.generation_recipes)
    except (ValueError, KeyError) as e:
        return JSONResponse(
            content={"error": "Generation failed", "detail": str(e)},
            status_code=400,
        )

    session.config = gen.config
    session.mermaid_diagrams = gen.diagrams
    session.flow_ir = gen.flow_irs
    session.expanded_flows = gen.expanded_flows
    known = set(session.org_registry.refs.keys()) if session.org_registry else None
    batches = dry_run(gen.config, known, skip_refs=session.skip_refs)
    session.batches = batches

    await persist_loader_draft(request, session)

    return {
        "status": "ok",
        "estimated_batches": len(batches),
        "estimated_api_calls": sum(len(b) for b in batches),
        "session_token": session_token,
    }
