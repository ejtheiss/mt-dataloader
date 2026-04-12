"""Private helpers and shared constants for Fund Flows routers (not an APIRouter)."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from dataloader.flows_mutation import get_base_config_for_generation
from dataloader.helpers import format_validation_errors
from dataloader.loader_validation import try_parse_pydantic_json_bytes
from dataloader.session import sessions
from flow_compiler import GenerationResult, generate_from_recipe
from models import DataLoaderConfig, GenerationRecipeV1
from org import reconcile_config, sync_connection_entities_from_reconciliation

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
    body = await request.body()
    recipe, err = try_parse_pydantic_json_bytes(GenerationRecipeV1, body)
    if err is not None:
        return JSONResponse(
            content={"error": "Invalid recipe", "detail": format_validation_errors(err)},
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

    base = get_base_config_for_generation(session)
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
