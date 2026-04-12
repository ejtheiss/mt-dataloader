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
from jsonutil import loads_str
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


def get_funds_flow_display_fields_for_display_row(
    session: Any,
    display_idx: int,
    expanded_row: Any | None,
) -> tuple[str | None, str | None]:
    """Resolve **display_title** / **display_summary** for one Fund Flows **list row**.

    **Canonical source:** ``funds_flows[edit_idx]`` in ``working_config_json`` (same row
    ``POST /api/flows/{flow_idx}/metadata`` mutates via
    ``resolve_working_funds_flow_index_for_metadata``). This keeps the list in sync after
    metadata saves without requiring a re-compile.

    **Fallback:** when working JSON is missing, invalid, or keys are absent, use attributes
    on the compiled **expanded** / pattern row (legacy / pre-persist display only).
    """
    text = (session.working_config_json or session.config_json_text or "").strip()
    if text:
        try:
            edit_idx = resolve_working_funds_flow_index_for_metadata(session, display_idx)
            config_dict = loads_str(text)
            flows = config_dict.get("funds_flows") or []
            if isinstance(flows, list) and 0 <= edit_idx < len(flows):
                row = flows[edit_idx]
                if isinstance(row, dict):
                    raw_t = row.get("display_title")
                    raw_s = row.get("display_summary")

                    def _norm(v: Any) -> str | None:
                        if v is None:
                            return None
                        if isinstance(v, str):
                            s = v.strip()
                            return s or None
                        return None

                    t, s = _norm(raw_t), _norm(raw_s)
                    if t is not None or s is not None:
                        return (t, s)
        except (ValueError, TypeError, KeyError):
            pass

    if expanded_row is not None:
        t2 = getattr(expanded_row, "display_title", None)
        s2 = getattr(expanded_row, "display_summary", None)
        if isinstance(t2, str):
            t2 = t2.strip() or None
        elif t2 is not None and not isinstance(t2, str):
            t2 = None
        if isinstance(s2, str):
            s2 = s2.strip() or None
        elif s2 is not None and not isinstance(s2, str):
            s2 = None
        return (t2, s2)
    return (None, None)


def resolve_working_funds_flow_index_for_metadata(session: Any, display_idx: int) -> int:
    """Map Fund Flows **list row index** to ``working_config_json['funds_flows']`` index (Plan 10c).

    When ``expanded_flows`` is populated, resolve via pattern ref so scaled-instance
    rows edit the correct pattern row. When it is empty (e.g. tests / pre-compile),
    fall back to direct indexing into ``funds_flows``.
    """
    text = (session.working_config_json or session.config_json_text or "").strip()
    if not text:
        raise ValueError("Session has no working config JSON")
    try:
        config_dict = loads_str(text)
    except (TypeError, ValueError) as e:
        raise ValueError("Invalid working config JSON") from e
    flows = config_dict.get("funds_flows") or []
    if not isinstance(flows, list):
        flows = []

    _, expanded = _display_flow_session_sources(session)
    if expanded and 0 <= display_idx < len(expanded):
        fc = expanded[display_idx]
        instance_ref = getattr(fc, "ref", None)
        if not instance_ref:
            raise ValueError(f"expanded_flows[{display_idx}] has no ref")
        pattern_ref = _recipe_flow_ref(str(instance_ref))
        for i, entry in enumerate(flows):
            if isinstance(entry, dict) and entry.get("ref") == pattern_ref:
                return i
        for i, entry in enumerate(flows):
            if isinstance(entry, dict) and entry.get("ref") == instance_ref:
                return i
        raise ValueError(
            f"No funds_flows entry for pattern_ref={pattern_ref!r} or ref={instance_ref!r}"
        )

    if 0 <= display_idx < len(flows):
        return display_idx
    raise ValueError(
        f"display_idx {display_idx} out of range (expanded len={len(expanded)}, "
        f"funds_flows len={len(flows)})"
    )


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
