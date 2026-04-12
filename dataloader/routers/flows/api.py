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
from dataloader.routers.deps import SessionHeaderDep
from dataloader.routers.flows.helpers import (
    _count_resources,
    _parse_and_compile_recipe,
    _parse_recipe,
)
from dataloader.routers.flows.schemas import RecipePatchBody, ScenarioSnapshotRequest
from dataloader.session import sessions
from dataloader.session.draft_persist import persist_loader_draft
from jsonutil import dumps_pretty, loads_str
from models import GenerationRecipeV1

router = APIRouter()


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
    if flow_idx < 0 or flow_idx >= len(flows):
        return JSONResponse(content={"error": "Invalid flow index"}, status_code=400)

    flow = flows[flow_idx]
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

    updated_json = dumps_pretty(config_dict)
    hdr_sess.working_config_json = updated_json

    await persist_loader_draft(request, hdr_sess)

    return {"status": "ok", "flow_idx": flow_idx}


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
