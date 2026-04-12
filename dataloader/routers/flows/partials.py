"""Fund Flows HTMX partials."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

import flow_compiler.seed_loader as seed_loader
from dataloader.flows_mutation import default_recipe_dict, recompose_and_persist_session
from dataloader.helpers import fmt_amt, format_validation_errors, get_flow_view_data
from dataloader.loader_validation import try_parse_pydantic_obj
from dataloader.routers.deps import OptionalSessionQueryDep, SessionHeaderDep, TemplatesDep
from dataloader.routers.flows.helpers import _display_flow_session_sources, _recipe_flow_ref
from dataloader.routers.flows.schemas import ActorConfigSaveBody
from dataloader.view_models.flows_config_drawer import build_flow_config_drawer_context
from flow_compiler import compute_flow_status, flow_account_deltas
from models import SOURCE_BADGE, GenerationRecipeV1

router = APIRouter()


@router.get("/api/flows/{flow_idx}/config-drawer", include_in_schema=False)
async def flow_config_drawer(
    request: Request,
    flow_idx: int,
    templates: TemplatesDep,
    sess: OptionalSessionQueryDep,
):
    """Plan 10e — wide HTMX partial for flow configuration (bands scaffold)."""
    if not sess:
        return HTMLResponse("<p>Session expired</p>", status_code=404)

    session_token = sess.session_token
    ctx = build_flow_config_drawer_context(sess, flow_idx, session_token)
    if ctx is None:
        return HTMLResponse("<p>Flow not found</p>", status_code=404)

    return templates.TemplateResponse(
        request,
        "partials/flow_config_drawer.html",
        ctx,
    )


@router.get("/api/flows/{flow_idx}/drawer", include_in_schema=False)
async def flow_drawer(
    request: Request,
    flow_idx: int,
    templates: TemplatesDep,
    sess: OptionalSessionQueryDep,
):
    """HTMX partial — flow summary for the slide-over drawer."""
    if not sess:
        return HTMLResponse("<p>Session expired</p>", status_code=404)

    session_token = sess.session_token
    display_flow_ir, display_expanded = _display_flow_session_sources(sess)

    if flow_idx < 0 or flow_idx >= len(display_flow_ir):
        return HTMLResponse("<p>Flow not found</p>", status_code=404)

    ir = display_flow_ir[flow_idx]
    fc = display_expanded[flow_idx] if flow_idx < len(display_expanded) else None

    actors_list = []
    if fc:
        for frame_name, frame in fc.actors.items():
            slot_types = []
            for _sn, slot in frame.slots.items():
                ref = slot.ref if hasattr(slot, "ref") else slot
                if "$ref:" in ref:
                    st = ref.replace("$ref:", "").split(".")[0]
                    slot_types.append(st)
            actors_list.append(
                {
                    "alias": frame.alias,
                    "frame_type": frame.frame_type,
                    "customer_name": frame.customer_name or "",
                    "slot_types": sorted(set(slot_types)),
                }
            )

    steps = []
    for step in ir.steps:
        steps.append(
            {
                "step_id": step.step_id,
                "resource_type": step.resource_type,
                "amount": getattr(step, "amount", None),
            }
        )

    return templates.TemplateResponse(
        request,
        "partials/flow_drawer.html",
        {
            "session_token": session_token,
            "flow_idx": flow_idx,
            "ir": ir,
            "actors": actors_list,
            "steps": steps,
            "deltas": flow_account_deltas(ir),
            "status": compute_flow_status(ir),
            "fmt_amt": fmt_amt,
        },
    )


@router.get("/api/flows/{flow_idx}/view/ledger", include_in_schema=False)
async def flow_ledger_view_partial(
    request: Request,
    flow_idx: int,
    templates: TemplatesDep,
    sess: OptionalSessionQueryDep,
):
    """HTMX partial — ledger view table."""
    if not sess:
        return HTMLResponse("<p>Session expired</p>", status_code=404)

    session_token = sess.session_token

    result = get_flow_view_data(sess, flow_idx)
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
async def flow_payments_view_partial(
    request: Request,
    flow_idx: int,
    templates: TemplatesDep,
    sess: OptionalSessionQueryDep,
):
    """HTMX partial — payments view table."""
    if not sess:
        return HTMLResponse("<p>Session expired</p>", status_code=404)

    session_token = sess.session_token

    result = get_flow_view_data(sess, flow_idx)
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


@router.get("/api/flows/{flow_idx}/actor-config", include_in_schema=False)
async def flow_actor_config_drawer(
    request: Request,
    flow_idx: int,
    templates: TemplatesDep,
    sess: OptionalSessionQueryDep,
):
    """HTMX partial: edit one actor's dataset / literal / name template."""
    frame = request.query_params.get("frame", "").strip()
    if not sess:
        return HTMLResponse(
            '<p class="text-muted">Session expired. Reload Setup.</p>', status_code=401
        )

    session_token = sess.session_token
    if not frame:
        return HTMLResponse("<p>Missing frame parameter.</p>", status_code=400)

    display_expanded = _display_flow_session_sources(sess)[1]
    if flow_idx < 0 or flow_idx >= len(display_expanded):
        return HTMLResponse("<p>Invalid flow index.</p>", status_code=400)

    fc = display_expanded[flow_idx]
    if frame not in fc.actors:
        return HTMLResponse("<p>Unknown actor frame.</p>", status_code=404)

    actor_model = fc.actors[frame]
    display_ir = _display_flow_session_sources(sess)[0]
    if flow_idx < len(display_ir):
        flow_ref = _recipe_flow_ref(display_ir[flow_idx].flow_ref)
    else:
        flow_ref = _recipe_flow_ref(fc.ref)
    recipe_raw = sess.generation_recipes.get(flow_ref)
    ov_raw: dict[str, Any] = {}
    if recipe_raw:
        ao = recipe_raw.get("actor_overrides") or {}
        if isinstance(ao.get(frame), dict):
            ov_raw = dict(ao[frame])

    return templates.TemplateResponse(
        request,
        "partials/flow_actor_config.html",
        {
            "session_token": session_token,
            "flow_idx": flow_idx,
            "flow_ref": flow_ref,
            "frame": frame,
            "actor_alias": actor_model.alias,
            "frame_type": actor_model.frame_type,
            "seed_datasets": seed_loader.list_datasets(),
            "customer_name": ov_raw.get("customer_name") or actor_model.customer_name or "",
            "entity_type": ov_raw.get("entity_type") or "business",
            "dataset": ov_raw.get("dataset") or actor_model.dataset or "standard",
            "name_template": ov_raw.get("name_template") or actor_model.name_template or "",
        },
    )


@router.post("/api/flows/{flow_idx}/actor-config", include_in_schema=False)
async def flow_actor_config_save(
    request: Request,
    flow_idx: int,
    hdr_sess: SessionHeaderDep,
):
    """Merge actor override into stored recipe and recompose session."""
    if not hdr_sess:
        return JSONResponse(
            content={"error": "Session not found. Please validate a config first."},
            status_code=401,
        )

    display_expanded = _display_flow_session_sources(hdr_sess)[1]
    if flow_idx < 0 or flow_idx >= len(display_expanded):
        return JSONResponse(content={"error": "Invalid flow index"}, status_code=400)

    fc = display_expanded[flow_idx]
    display_ir = _display_flow_session_sources(hdr_sess)[0]
    if flow_idx < len(display_ir):
        flow_ref = _recipe_flow_ref(display_ir[flow_idx].flow_ref)
    else:
        flow_ref = _recipe_flow_ref(fc.ref)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            content={"error": "Invalid body", "detail": "Request body must be JSON."},
            status_code=422,
        )
    parsed, err = try_parse_pydantic_obj(ActorConfigSaveBody, body)
    if err is not None:
        return JSONResponse(
            content={"error": "Invalid body", "detail": format_validation_errors(err)},
            status_code=422,
        )

    frame = parsed.frame
    if frame not in fc.actors:
        return JSONResponse(content={"error": "Unknown actor frame"}, status_code=404)

    if flow_ref not in hdr_sess.generation_recipes:
        hdr_sess.generation_recipes[flow_ref] = default_recipe_dict(flow_ref)

    recipe_dict = dict(hdr_sess.generation_recipes[flow_ref])
    actor_overrides = dict(recipe_dict.get("actor_overrides") or {})
    clean = parsed.override.model_dump(exclude_none=True)
    for key in ("customer_name", "name_template"):
        if clean.get(key) == "":
            clean.pop(key, None)
    if clean.get("dataset") in ("", "standard", None):
        clean.pop("dataset", None)
    if clean:
        actor_overrides[frame] = clean
    else:
        actor_overrides.pop(frame, None)
    recipe_dict["actor_overrides"] = actor_overrides
    _, err = try_parse_pydantic_obj(GenerationRecipeV1, recipe_dict)
    if err is not None:
        return JSONResponse(
            content={
                "error": "Invalid recipe after merge",
                "detail": format_validation_errors(err),
            },
            status_code=422,
        )
    hdr_sess.generation_recipes[flow_ref] = recipe_dict

    composed = await recompose_and_persist_session(request, hdr_sess)
    if isinstance(composed, JSONResponse):
        return composed

    return {"status": "ok", "flow_ref": flow_ref, "frame": frame}
