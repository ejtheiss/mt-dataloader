"""Fund Flows generation recipe merge, multi-recipe compose, and session recompose (Plan 05).

Kept separate from ``dataloader.routers.flows`` so routes stay thin and tests can patch
``recompose_and_persist_session`` without importing the full router module graph.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

from dataloader.engine import all_resources, dry_run, typed_ref_for
from dataloader.helpers import build_preview
from dataloader.loader_validation import parse_loader_config_json_text, require_pydantic_obj
from dataloader.session.draft_persist import persist_loader_draft
from flow_compiler import GenerationResult, generate_from_recipe
from flow_compiler.flow_views import compute_view_data
from models import DataLoaderConfig, GenerationRecipeV1
from org import reconcile_config, sync_connection_entities_from_reconciliation


def prepare_actor_library_for_compose(session: Any) -> None:
    """Plan 11a — hydrate library from legacy shapes, then project bindings into recipes."""
    from dataloader.actor_library_runtime import (
        ensure_actor_library_hydrated_from_legacy,
        materialize_actor_bindings_to_generation_recipes,
        sync_legacy_library_rows_from_recipes,
    )

    ensure_actor_library_hydrated_from_legacy(session)
    sync_legacy_library_rows_from_recipes(session)
    materialize_actor_bindings_to_generation_recipes(session)


def get_base_config_for_generation(session: Any) -> DataLoaderConfig:
    """Config with ``funds_flows`` intact for recipe ``flow_ref`` pattern lookup.

    After validate, ``base_config_json`` / ``config_json_text`` are the emitted
    (flattened) config with empty ``funds_flows``; prefer ``authoring_config_json``.
    """
    acj = getattr(session, "authoring_config_json", None)
    if acj:
        pr = parse_loader_config_json_text(acj)
        if pr.error is None and pr.config is not None and pr.config.funds_flows:
            return pr.config
    source = session.base_config_json or session.config_json_text
    if source is None:
        return session.config.model_copy(deep=True)
    pr2 = parse_loader_config_json_text(source)
    if pr2.error is None and pr2.config is not None:
        return pr2.config
    return session.config.model_copy(deep=True)


def merge_infra_with_flows(
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
    return require_pydantic_obj(DataLoaderConfig, data)


def compose_all_recipes(
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
        recipe = require_pydantic_obj(GenerationRecipeV1, recipe_dict)
        merged = merge_infra_with_flows(running_config, base)
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


def default_recipe_dict(flow_ref: str) -> dict[str, Any]:
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


def merge_recipe_dict(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Shallow + targeted deep merge for ``GenerationRecipeV1``-shaped dicts (plan 05 recipe-patch).

    Dict-valued keys ``actor_overrides``, ``step_variance``, ``edge_case_overrides`` merge; ``timing``
    merges and ``timing.step_offsets`` merges; other keys are replaced by ``patch``.
    """
    out = dict(base)
    for key, val in patch.items():
        if val is None:
            continue
        if key == "actor_overrides" and isinstance(val, dict):
            merged = dict(out.get("actor_overrides") or {})
            for ak, av in val.items():
                if isinstance(av, dict) and isinstance(merged.get(ak), dict):
                    merged[ak] = {**merged[ak], **av}
                else:
                    merged[ak] = av
            out["actor_overrides"] = merged
        elif key == "step_variance" and isinstance(val, dict):
            merged = dict(out.get("step_variance") or {})
            merged.update(val)
            out["step_variance"] = merged
        elif key == "edge_case_overrides" and isinstance(val, dict):
            merged = dict(out.get("edge_case_overrides") or {})
            merged.update(val)
            out["edge_case_overrides"] = merged
        elif key == "timing" and isinstance(val, dict):
            cur = dict(out.get("timing") or {})
            for tk, tv in val.items():
                if tk == "step_offsets" and isinstance(tv, dict):
                    so = dict(cur.get("step_offsets") or {})
                    so.update(tv)
                    cur["step_offsets"] = so
                else:
                    cur[tk] = tv
            out["timing"] = cur
        else:
            out[key] = val
    fr = base.get("flow_ref") or patch.get("flow_ref") or out.get("flow_ref")
    if fr:
        out["flow_ref"] = fr
    return out


async def recompose_and_persist_session(
    request: Request,
    session: Any,
) -> JSONResponse | GenerationResult:
    """Run ``compose_all_recipes`` and mirror results onto ``session``.

    Returns ``JSONResponse`` on generation failure; otherwise the
    ``GenerationResult`` that was applied.
    """
    prepare_actor_library_for_compose(session)
    base = get_base_config_for_generation(session)
    try:
        gen = compose_all_recipes(base, session.generation_recipes)
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
