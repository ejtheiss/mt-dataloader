"""Fund Flows routes: list, detail, view partials, and generation API."""

from __future__ import annotations

import json
import secrets
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field, ValidationError

import flow_compiler.seed_loader as seed_loader
from dataloader.engine import all_resources, dry_run, typed_ref_for
from dataloader.helpers import (
    build_preview,
    fmt_amt,
    format_validation_errors,
    get_flow_view_data,
)
from dataloader.routers.deps import (
    OptionalSessionQueryDep,
    SessionHeaderDep,
    TemplatesDep,
)
from dataloader.session import sessions
from dataloader.session.draft_persist import persist_loader_draft
from flow_compiler import (
    GenerationResult,
    compile_diagnostics,
    compute_flow_status,
    flow_account_deltas,
    generate_from_recipe,
)
from flow_compiler.flow_views import compute_view_data
from jsonutil import dumps_pretty, loads_str
from models import (
    SOURCE_BADGE,
    ActorDatasetOverride,
    DataLoaderConfig,
    GenerationRecipeV1,
)
from org import reconcile_config, sync_connection_entities_from_reconciliation

router = APIRouter(tags=["flows"])

_GEN_SECTIONS = (
    "payment_orders",
    "incoming_payment_details",
    "ledger_transactions",
    "expected_payments",
    "returns",
    "reversals",
    "transition_ledger_transactions",
)


def _count_resources(config: DataLoaderConfig) -> dict[str, int]:
    return {s: len(getattr(config, s, None) or []) for s in _GEN_SECTIONS}


def _recipe_flow_ref(emitted_flow_ref: str) -> str:
    """Map ``pattern__0042`` → ``pattern`` for ``generation_recipes`` / API keys."""
    parts = emitted_flow_ref.rsplit("__", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return emitted_flow_ref


def _display_flow_session_sources(session: Any) -> tuple[list, list]:
    """IR + expanded flows for Fund Flows UI.

    After scenario apply, ``session.flow_ir`` holds generated instances (Faker, etc.)
    while ``pattern_*`` stays the single pattern compile from validate. Prefer generated
    whenever the user has recipes and a non-empty ``flow_ir``.
    """
    pattern_ir = session.pattern_flow_ir or []
    pattern_exp = session.pattern_expanded_flows or []
    flow_ir = session.flow_ir or []
    expanded = session.expanded_flows or []
    recipes = getattr(session, "generation_recipes", None) or {}
    if recipes and flow_ir:
        return flow_ir, expanded
    return (pattern_ir or flow_ir), (pattern_exp or expanded)


def _step_variance_ui_fields(step_id: str, recipe: dict[str, Any] | None) -> dict[str, Any]:
    """Map saved generation recipe ``step_variance`` to scenario-builder row fields.

    Absent key → follow global variance; empty dict → locked; non-empty → custom % inputs.
    """
    base: dict[str, Any] = {
        "variance_mode": "global",
        "variance_custom_min": 0.0,
        "variance_custom_max": 0.0,
    }
    if not recipe:
        return base
    sv = recipe.get("step_variance")
    if not isinstance(sv, dict) or step_id not in sv:
        return base
    raw = sv.get(step_id)
    if raw is None or (isinstance(raw, dict) and len(raw) == 0):
        base["variance_mode"] = "locked"
        return base
    if isinstance(raw, dict):
        base["variance_mode"] = "custom"
        base["variance_custom_min"] = float(raw.get("min_pct") or 0)
        base["variance_custom_max"] = float(raw.get("max_pct") or 0)
    return base


def _get_base_config(session: Any) -> DataLoaderConfig:
    """Config with ``funds_flows`` intact for recipe ``flow_ref`` pattern lookup.

    After validate, ``base_config_json`` / ``config_json_text`` are the emitted
    (flattened) config with empty ``funds_flows``; prefer ``authoring_config_json``.
    """
    acj = getattr(session, "authoring_config_json", None)
    if acj:
        try:
            cfg = DataLoaderConfig.model_validate_json(acj)
            if cfg.funds_flows:
                return cfg
        except ValidationError:
            pass
    source = session.base_config_json or session.config_json_text
    try:
        return DataLoaderConfig.model_validate_json(source)
    except ValidationError:
        return session.config.model_copy(deep=True)


def _compose_all_recipes(
    base: DataLoaderConfig,
    recipes: dict[str, dict],
) -> GenerationResult:
    """Generate from every stored recipe sequentially.

    Each recipe is applied to the running config so that shared
    infrastructure is emitted once and all flow instances accumulate.
    Returns a merged ``GenerationResult`` with combined outputs.

    The original ``base`` config keeps its ``funds_flows`` intact so
    that every recipe can look up its pattern flow.  Only the
    *infrastructure* sections (legal_entities, counterparties, etc.)
    accumulate across recipes.
    """
    running_config = base
    all_flow_irs: list = []
    all_expanded_flows: list = []
    all_diagrams: list[str] = []
    combined_edge_map: dict[str, list[int]] = {}

    for _flow_ref, recipe_dict in recipes.items():
        recipe = GenerationRecipeV1.model_validate(recipe_dict)
        merged = _merge_infra_with_flows(running_config, base)
        gen = generate_from_recipe(recipe, base_config=merged)
        running_config = gen.config
        all_flow_irs.extend(gen.flow_irs)
        all_expanded_flows.extend(gen.expanded_flows)
        all_diagrams.extend(gen.diagrams)
        for label, indices in gen.edge_case_map.items():
            combined_edge_map.setdefault(label, []).extend(indices)

    return GenerationResult(
        config=running_config,
        diagrams=all_diagrams,
        edge_case_map=combined_edge_map,
        flow_irs=all_flow_irs,
        expanded_flows=all_expanded_flows,
    )


def _merge_infra_with_flows(
    running: DataLoaderConfig,
    original: DataLoaderConfig,
) -> DataLoaderConfig:
    """Merge accumulated infrastructure from ``running`` with the
    original ``funds_flows`` so subsequent recipes can find their
    flow patterns.

    After the first recipe, ``running.funds_flows`` is empty because
    ``emit_dataloader_config`` clears it.  We restore from ``original``.
    """
    if running.funds_flows:
        return running
    data = running.model_dump(exclude_none=True)
    data["funds_flows"] = [f.model_dump(exclude_none=True) for f in original.funds_flows]
    return DataLoaderConfig.model_validate(data)


async def _parse_recipe(
    request: Request,
) -> tuple[str, Any, GenerationRecipeV1] | JSONResponse:
    """Parse and validate a recipe from the request body."""
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
    return token, session, recipe


async def _parse_and_compile_recipe(
    request: Request,
) -> tuple[str, Any, GenerationRecipeV1, GenerationResult] | JSONResponse:
    """Shared parse -> compile -> reconcile helper for single-recipe endpoints."""
    result = await _parse_recipe(request)
    if isinstance(result, JSONResponse):
        return result
    token, session, recipe = result

    base = _get_base_config(session)
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
                for ck, cid in m.child_refs.items():
                    session.registry.register_or_update(f"{m.config_ref}.{ck}", cid)
        session.reconciliation = reconciliation
        session.skip_refs = skip_refs
        sync_connection_entities_from_reconciliation(
            gen_result.config,
            session.discovery,
            reconciliation,
            {},
        )

    return token, session, recipe, gen_result


def _default_recipe_dict(flow_ref: str) -> dict[str, Any]:
    """Minimal recipe matching scenario-builder defaults when none exists yet."""
    return {
        "version": "v1",
        "flow_ref": flow_ref,
        "instances": 10,
        "seed": 424242,
        "seed_dataset": "standard",
        "edge_case_count": 0,
        "amount_variance_min_pct": 0.0,
        "amount_variance_max_pct": 0.0,
    }


async def _recompose_and_persist_session(
    request: Request,
    session: Any,
) -> JSONResponse | GenerationResult:
    """Run ``_compose_all_recipes`` and mirror results onto ``session``.

    Returns ``JSONResponse`` on generation failure; otherwise the
    ``GenerationResult`` that was applied.
    """
    base = _get_base_config(session)
    try:
        gen = _compose_all_recipes(base, session.generation_recipes)
    except (ValueError, KeyError) as e:
        return JSONResponse(
            content={"error": "Generation failed", "detail": str(e)},
            status_code=400,
        )

    if session.discovery is not None:
        reconciliation = reconcile_config(gen.config, session.discovery)
        skip_refs: set[str] = set()
        for m in reconciliation.matches:
            if m.use_existing:
                session.registry.register_or_update(m.config_ref, m.discovered_id)
                skip_refs.add(m.config_ref)
                for ck, cid in m.child_refs.items():
                    session.registry.register_or_update(f"{m.config_ref}.{ck}", cid)
        session.reconciliation = reconciliation
        session.skip_refs = skip_refs
        sync_connection_entities_from_reconciliation(
            gen.config,
            session.discovery,
            reconciliation,
            {},
        )

    session.config = gen.config
    config_json_text = gen.config.model_dump_json(indent=2, exclude_none=True)
    session.config_json_text = config_json_text
    session.working_config_json = config_json_text
    session.mermaid_diagrams = gen.diagrams
    session.flow_ir = gen.flow_irs
    session.expanded_flows = gen.expanded_flows
    session.view_data_cache = [
        compute_view_data(ir, fc) for ir, fc in zip(gen.flow_irs, gen.expanded_flows)
    ]

    known = set(session.org_registry.refs.keys()) if session.org_registry else None
    batches = dry_run(gen.config, known, skip_refs=session.skip_refs)
    session.batches = batches
    resource_map = {typed_ref_for(r): r for r in all_resources(gen.config)}
    session.preview_items = build_preview(
        batches,
        resource_map,
        skip_refs=session.skip_refs,
        reconciliation=session.reconciliation,
        update_refs=session.update_refs,
    )
    await persist_loader_draft(request, session)
    return gen


class ActorConfigSaveBody(BaseModel):
    model_config = {"extra": "forbid"}

    frame: str = Field(min_length=1)
    override: ActorDatasetOverride = Field(default_factory=ActorDatasetOverride)


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
        for i, ir in enumerate(display_flow_ir):
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

            flow_summaries.append(
                {
                    "index": i,
                    "flow_ref": ir.flow_ref,
                    "recipe_flow_ref": recipe_key,
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
                        i < len(display_expanded) and display_expanded[i].instance_resources
                    ),
                }
            )

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

    trace_metadata = dict(flow_ir.trace_metadata) if flow_ir.trace_metadata else {}
    trace_value_template = flow_config.trace_value_template if flow_config else flow_ir.trace_value

    per_step_metadata: list[dict] = []
    for step in flow_ir.steps:
        step_meta = dict(step.trace_metadata) if step.trace_metadata else {}
        step_meta = {k: v for k, v in step_meta.items() if not k.startswith("_flow_")}
        per_step_metadata.append(
            {
                "step_id": step.step_id,
                "resource_type": step.resource_type,
                "metadata": step_meta,
            }
        )

    actor_aliases = list(flow_config.actors.keys()) if flow_config else []

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
            "metadata_key": (
                flow_config.view_config.ledger_view.metadata_key
                if flow_config
                and flow_config.view_config
                and flow_config.view_config.ledger_view
                and flow_config.view_config.ledger_view.metadata_key
                else flow_ir.trace_key
            ),
            "metadata_value": flow_ir.trace_value,
            "trace_value_template": trace_value_template,
            "trace_metadata": trace_metadata,
            "per_step_metadata": per_step_metadata,
            "actor_aliases": actor_aliases,
            "fmt_amt": fmt_amt,
            "source_badge": SOURCE_BADGE,
        },
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


@router.post("/api/flows/{flow_idx}/metadata")
async def update_flow_metadata(
    request: Request,
    flow_idx: int,
    hdr_sess: SessionHeaderDep,
):
    """Update trace_key, trace_value_template, trace_metadata, and per-step metadata."""
    if not hdr_sess:
        return JSONResponse(
            content={"error": "Session not found"},
            status_code=401,
        )

    body = await request.json()
    trace_key = body.get("trace_key")
    trace_value_template = body.get("trace_value_template")
    trace_metadata = body.get("trace_metadata")
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

    updated_json = dumps_pretty(config_dict)
    hdr_sess.working_config_json = updated_json

    await persist_loader_draft(request, hdr_sess)

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


@router.post("/api/flows/recipe-to-working-config")
async def recipe_to_working_config(request: Request):
    """Store a recipe for one flow, then compose all stored recipes."""
    result = await _parse_recipe(request)
    if isinstance(result, JSONResponse):
        return result
    session_token, session, recipe = result

    session.generation_recipes[recipe.flow_ref] = recipe.model_dump()

    composed = await _recompose_and_persist_session(request, session)
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
        parsed = ActorConfigSaveBody.model_validate(body)
    except ValidationError as e:
        return JSONResponse(
            content={"error": "Invalid body", "detail": format_validation_errors(e)},
            status_code=422,
        )

    frame = parsed.frame
    if frame not in fc.actors:
        return JSONResponse(content={"error": "Unknown actor frame"}, status_code=404)

    if flow_ref not in hdr_sess.generation_recipes:
        hdr_sess.generation_recipes[flow_ref] = _default_recipe_dict(flow_ref)

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
    try:
        GenerationRecipeV1.model_validate(recipe_dict)
    except ValidationError as e:
        return JSONResponse(
            content={"error": "Invalid recipe after merge", "detail": format_validation_errors(e)},
            status_code=422,
        )
    hdr_sess.generation_recipes[flow_ref] = recipe_dict

    composed = await _recompose_and_persist_session(request, hdr_sess)
    if isinstance(composed, JSONResponse):
        return composed

    return {"status": "ok", "flow_ref": flow_ref, "frame": frame}


@router.post("/api/flows/generate-execute")
async def generate_execute(request: Request):
    """Compile recipe and feed directly into the execution pipeline."""
    result = await _parse_recipe(request)
    if isinstance(result, JSONResponse):
        return result
    session_token, session, recipe = result

    session.generation_recipes[recipe.flow_ref] = recipe.model_dump()

    base = _get_base_config(session)
    try:
        gen = _compose_all_recipes(base, session.generation_recipes)
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
