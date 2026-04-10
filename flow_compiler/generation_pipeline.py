"""Recipe → N instances → compile → emit → Mermaid (Plan 08 Track B4-1 / B4-2).

Phase order matches ``generation.py`` module docstring (P0–P13).  Orchestration
lives here; step implementations remain in ``generation.py`` (clone, variance,
edge preselect, …) to avoid a ``generation/`` package shadowing ``generation.py``.
"""

from __future__ import annotations

import random
from typing import Any

from models import DataLoaderConfig, FundsFlowConfig, GenerationRecipeV1

from .core import compile_flows, emit_dataloader_config, flatten_optional_groups
from .generation import (
    GenerationResult,
    _apply_payment_mix,
    _build_instance_profile,
    _expand_instance_resources,
    activate_optional_groups,
    apply_amount_variance,
    apply_overrides,
    clone_flow,
    mark_staged,
    preselect_edge_cases,
    select_staged_instances,
)
from .ir import FlowIR
from .timing import compute_effective_dates, compute_spread_offsets


def run_generation_pipeline(
    recipe: GenerationRecipeV1,
    base_config: DataLoaderConfig,
) -> GenerationResult:
    """Execute P0–P13; same behavior as the historical ``generate_from_recipe`` body."""
    pattern = _p0_resolve_pattern(recipe, base_config)
    edge_overrides = _p0_edge_overrides(recipe)

    edge_selections = _p1_preselect_edge_cases(pattern, recipe, edge_overrides)
    staged_instances = _p2_staging(recipe, edge_selections)
    recipe_timing, flow_timing, spread_offsets = _p3_timing_spread(recipe, pattern)

    extra_resources: dict[str, list[dict]] = {}
    flows: list[FundsFlowConfig] = []
    for i in range(recipe.instances):
        flow_dict = _p4_through_p11_one_instance(
            i,
            recipe,
            pattern,
            edge_selections,
            staged_instances,
            recipe_timing,
            flow_timing,
            spread_offsets,
            extra_resources,
        )
        flows.append(FundsFlowConfig.model_validate(flow_dict))

    flow_irs, compiled = _p12_compile_and_emit(flows, base_config, extra_resources)
    diagrams = _p13_mermaid_diagrams(flow_irs, flows)

    edge_case_map = {
        label: sorted(indices) for label, indices in edge_selections.items() if indices
    }
    return GenerationResult(
        config=compiled,
        diagrams=diagrams,
        edge_case_map=edge_case_map,
        flow_irs=flow_irs,
        expanded_flows=flows,
    )


def _p0_resolve_pattern(
    recipe: GenerationRecipeV1,
    base_config: DataLoaderConfig,
) -> FundsFlowConfig:
    pattern = next(
        (f for f in base_config.funds_flows if f.ref == recipe.flow_ref),
        None,
    )
    if pattern is None:
        available = [f.ref for f in base_config.funds_flows]
        raise ValueError(
            f"flow_ref '{recipe.flow_ref}' not found in loaded config. Available: {available}"
        )
    return pattern


def _p0_edge_overrides(recipe: GenerationRecipeV1) -> dict[str, Any] | None:
    if not recipe.edge_case_overrides:
        return None
    return {k: v.model_dump() for k, v in recipe.edge_case_overrides.items()}


def _p1_preselect_edge_cases(
    pattern: FundsFlowConfig,
    recipe: GenerationRecipeV1,
    edge_overrides: dict[str, Any] | None,
) -> dict[str, set[int]]:
    return preselect_edge_cases(
        pattern.model_dump(),
        recipe.edge_case_count,
        recipe.instances,
        recipe.seed,
        overrides=edge_overrides,
    )


def _p2_staging(
    recipe: GenerationRecipeV1,
    edge_selections: dict[str, set[int]],
) -> set[int]:
    return select_staged_instances(
        recipe,
        recipe.instances,
        random.Random(recipe.seed),
        edge_selections=edge_selections,
    )


def _p3_timing_spread(
    recipe: GenerationRecipeV1,
    pattern: FundsFlowConfig,
) -> tuple[Any, Any, list[float]]:
    recipe_timing = recipe.timing
    flow_timing = pattern.timing
    spread_offsets: list[float] = []
    if recipe_timing and recipe_timing.instance_spread_days > 0:
        spread_offsets = compute_spread_offsets(
            recipe.instances,
            recipe_timing.instance_spread_days,
            recipe_timing.spread_pattern,
            recipe.seed,
            jitter_days=recipe_timing.spread_jitter_days,
        )
    return recipe_timing, flow_timing, spread_offsets


def _p4_through_p11_one_instance(
    i: int,
    recipe: GenerationRecipeV1,
    pattern: FundsFlowConfig,
    edge_selections: dict[str, set[int]],
    staged_instances: set[int],
    recipe_timing: Any,
    flow_timing: Any,
    spread_offsets: list[float],
    extra_resources: dict[str, list[dict]],
) -> dict:
    """Per-instance P4–P11: profile, clone, resources, overrides, variance, OG, …"""
    rng = random.Random(recipe.seed + i)
    profile = _build_instance_profile(pattern, recipe, i)
    flow_dict, instance_resources = clone_flow(pattern, i, profile)

    if instance_resources:
        expanded = _expand_instance_resources(instance_resources, i, profile, pattern=pattern)
        for section, items in expanded.items():
            bucket = extra_resources.setdefault(section, [])
            seen = {it.get("ref") for it in bucket if isinstance(it, dict) and it.get("ref")}
            for item in items:
                ref = item.get("ref") if isinstance(item, dict) else None
                if ref and ref in seen:
                    continue
                bucket.append(item)
                if ref:
                    seen.add(ref)

    if recipe.overrides:
        apply_overrides(flow_dict, recipe.overrides)

    has_variance = (
        recipe.amount_variance_min_pct < 0
        or recipe.amount_variance_max_pct > 0
        or recipe.step_variance
    )
    if has_variance:
        apply_amount_variance(
            flow_dict,
            recipe.amount_variance_min_pct,
            recipe.amount_variance_max_pct,
            rng,
            step_variance=recipe.step_variance or None,
        )

    activated = {label for label, indices in edge_selections.items() if i in indices}
    activated = activate_optional_groups(flow_dict, activated)

    for og in flow_dict.get("optional_groups", []):
        if og["label"] in activated:
            group_count = len(edge_selections.get(og["label"], set()))
            for step in og["steps"]:
                step.setdefault("metadata", {})
                step["metadata"]["_flow_optional_group"] = og["label"]
                step["metadata"]["_flow_edge_case_count"] = str(group_count)
                step["metadata"]["_flow_trigger"] = og.get("trigger", "manual")

    flatten_optional_groups(flow_dict, activated)

    if i in staged_instances:
        mark_staged(flow_dict)

    if recipe.payment_mix:
        _apply_payment_mix(flow_dict, recipe.payment_mix)

    has_explicit_dates = (
        (recipe_timing and recipe_timing.start_date)
        or (recipe_timing and recipe_timing.instance_spread_days > 0)
        or (recipe_timing and recipe_timing.step_offsets)
        or flow_timing
    )
    if has_explicit_dates:
        spread = spread_offsets[i] if i < len(spread_offsets) else 0.0
        compute_effective_dates(
            flow_dict,
            instance_index=i,
            spread_offset_days=spread,
            flow_timing=flow_timing,
            recipe_timing=recipe_timing,
            seed=recipe.seed,
        )
        flow_dict.pop("_computed_dates", None)
        flow_dict.pop("_base_date", None)

    return flow_dict


def _p12_compile_and_emit(
    flows: list[FundsFlowConfig],
    base_config: DataLoaderConfig,
    extra_resources: dict[str, list[dict]],
) -> tuple[list[FlowIR], DataLoaderConfig]:
    flow_irs = compile_flows(flows, base_config)
    compiled = emit_dataloader_config(
        flow_irs,
        base_config=base_config,
        extra_resources=extra_resources,
    )
    return flow_irs, compiled


def _p13_mermaid_diagrams(
    flow_irs: list[FlowIR],
    flows: list[FundsFlowConfig],
) -> list[str]:
    from .mermaid import render_mermaid

    diagrams: list[str] = []
    for ir, flow_config in zip(flow_irs[:10], flows[:10]):
        diagrams.append(render_mermaid(ir, flow_config))
    return diagrams
