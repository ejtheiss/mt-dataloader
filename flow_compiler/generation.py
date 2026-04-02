"""Generation pipeline: clone, variance, activation, staging, recipe expansion.

Handles the transformation from a ``GenerationRecipeV1`` into N flow
instances plus their compiled output.
"""

from __future__ import annotations

import copy
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

import seed_loader
from models import (
    PAYMENT_MIX_TYPE_MAP,
    DataLoaderConfig,
    FundsFlowConfig,
    GenerationRecipeV1,
    PaymentMixConfig,
)

from .core import compile_flows, emit_dataloader_config, flatten_optional_groups
from .ir import FlowIR
from .mermaid import render_mermaid
from .timing import compute_effective_dates, compute_spread_offsets


@dataclass(frozen=True)
class GenerationResult:
    """Structured result from ``generate_from_recipe``."""

    config: DataLoaderConfig
    diagrams: list[str]
    edge_case_map: dict[str, list[int]]
    flow_irs: list[FlowIR]
    expanded_flows: list[FundsFlowConfig]


def deep_format_map(obj: Any, mapping: dict[str, str]) -> Any:
    """Recursively apply str.format_map to all string values.

    Unknown placeholders are left empty via defaultdict(str) so patterns
    with {business_name} don't crash when only individual data is provided.
    """
    safe = defaultdict(str, mapping)
    if isinstance(obj, str):
        try:
            return obj.format_map(safe)
        except (ValueError, KeyError):
            return obj
    if isinstance(obj, dict):
        return {k: deep_format_map(v, mapping) for k, v in obj.items()}
    if isinstance(obj, list):
        return [deep_format_map(v, mapping) for v in obj]
    return obj


def _expand_instance_resources(
    instance_resources: dict[str, list[dict]],
    instance: int,
    profile: dict[str, str],
) -> dict[str, list[dict]]:
    """Clone instance_resources templates with profile substitution."""
    mapping = {"instance": f"{instance:04d}", **profile}
    result: dict[str, list[dict]] = {}
    for section, templates in instance_resources.items():
        section_items: list[dict] = []
        for tpl in templates:
            cloned = copy.deepcopy(tpl)
            cloned = deep_format_map(cloned, mapping)
            section_items.append(cloned)
        result[section] = section_items
    return result


def clone_flow(
    flow: FundsFlowConfig,
    instance: int,
    profile: dict[str, str] | None = None,
) -> tuple[dict, dict[str, list[dict]] | None]:
    """Clone a flow for a specific instance with optional profile substitution."""
    as_dict = flow.model_dump()
    as_dict["ref"] = f"{flow.ref}__{instance:04d}"

    ir = as_dict.pop("instance_resources", None)

    if profile:
        mapping = {"instance": f"{instance:04d}", "ref": as_dict["ref"], **profile}
        as_dict = deep_format_map(as_dict, mapping)

    return as_dict, ir


def apply_overrides(flow_dict: dict, overrides: dict[str, Any]) -> dict:
    """Apply dotted-path key-value overrides to a flow dict."""
    for dotted_key, value in overrides.items():
        parts = dotted_key.split(".")
        target: Any = flow_dict
        for part in parts[:-1]:
            if isinstance(target, list) and part.isdigit():
                target = target[int(part)]
            else:
                target = target[part]
        final = parts[-1]
        if isinstance(target, list) and final.isdigit():
            target[int(final)] = value
        else:
            target[final] = value
    return flow_dict


def apply_amount_variance(
    flow_dict: dict,
    min_pct: float,
    max_pct: float,
    rng: random.Random,
    step_variance: dict[str, dict[str, float]] | None = None,
) -> dict:
    """Apply min/max percentage variance to all amount fields in steps.

    *min_pct* is negative or zero (e.g. -10.0 = down to 90% of base).
    *max_pct* is positive or zero (e.g. 10.0 = up to 110% of base).

    *step_variance* maps step_id → {"min_pct": ..., "max_pct": ...}.
    An empty dict locks the step to zero variance.

    Uses the SAME jitter factor for all amounts within a single step
    to keep ledger entries balanced (DR and CR get the same percentage).
    """
    if min_pct >= 0 and max_pct <= 0 and not step_variance:
        return flow_dict
    all_steps = list(flow_dict.get("steps") or [])
    for og in flow_dict.get("optional_groups") or []:
        all_steps.extend(og.get("steps") or [])
    for step in all_steps:
        entries = step.get("ledger_entries") or []
        if not step.get("amount") and not entries:
            continue
        step_id = step.get("step_id", "")
        sv = step_variance.get(step_id) if step_variance else None
        if sv is not None:
            if not sv:
                continue
            lo = sv.get("min_pct", 0.0) / 100.0
            hi = sv.get("max_pct", 0.0) / 100.0
        else:
            lo = min_pct / 100.0
            hi = max_pct / 100.0
        if lo >= 0 and hi <= 0:
            continue
        jitter = rng.uniform(lo, hi)
        if "amount" in step and isinstance(step["amount"], (int, float)):
            step["amount"] = max(1, round(step["amount"] * (1 + jitter)))
        for entry in entries:
            if "amount" in entry and isinstance(entry["amount"], (int, float)):
                entry["amount"] = max(1, round(entry["amount"] * (1 + jitter)))
    return flow_dict


def _step_matches(step: dict, match: dict) -> bool:
    """Check if a single step satisfies all non-None conditions in a StepMatch."""
    for field in ("payment_type", "direction", "resource_type"):
        required = match.get(field)
        if required is None:
            continue
        step_val = step.get(field) if field != "resource_type" else step.get("type")
        if step_val != required:
            return False
    return True


def _is_applicable(og: dict, steps: list[dict]) -> bool:
    """Check applicability rules for an optional group against current steps."""
    rule = og.get("applicable_when")
    if not rule:
        return True

    requires = rule.get("requires_step_match")
    if requires:
        found = False
        for match in requires:
            if any(_step_matches(s, match) for s in steps):
                found = True
                break
        if not found:
            return False

    excludes = rule.get("excludes_step_match")
    if excludes:
        for match in excludes:
            if any(_step_matches(s, match) for s in steps):
                return False

    dep_step = rule.get("depends_on_step")
    if dep_step:
        step_ids = {s.get("step_id") for s in steps}
        if dep_step not in step_ids:
            return False

    return True


def _resolve_count(
    label: str,
    global_count: int,
    overrides: dict | None = None,
) -> tuple[int, bool]:
    """Return (count, enabled) for a group, considering per-group overrides."""
    if overrides and label in overrides:
        ov = overrides[label]
        if not ov.get("enabled", True):
            return 0, False
        c = ov.get("count")
        return (c if c is not None else global_count), True
    return global_count, True


def preselect_edge_cases(
    flow_dict: dict,
    global_count: int,
    total_instances: int,
    seed: int,
    overrides: dict | None = None,
) -> dict[str, set[int]]:
    """Pre-select exactly which instance indices get each edge case.

    Returns ``{group_label: set_of_instance_indices}``.
    Exclusion-group members get disjoint instance sets (weighted by ``weight``).
    Independent groups select from the full instance pool and may overlap.
    """
    rng = random.Random(seed + 7777)
    steps = flow_dict.get("steps", [])
    result: dict[str, set[int]] = {}

    applicable: list[dict] = []
    for og in flow_dict.get("optional_groups", []):
        label = og["label"]
        if not _is_applicable(og, steps):
            result[label] = set()
            continue
        count, enabled = _resolve_count(label, global_count, overrides)
        count = min(count, total_instances)
        if not enabled or count <= 0:
            result[label] = set()
            continue
        applicable.append({**og, "_count": count})

    exclusion_groups: dict[str, list[dict]] = defaultdict(list)
    independent: list[dict] = []
    for og in applicable:
        eg = og.get("exclusion_group")
        if eg:
            exclusion_groups[eg].append(og)
        else:
            independent.append(og)

    all_indices = list(range(total_instances))

    for _eg_name, members in exclusion_groups.items():
        total_needed = sum(m["_count"] for m in members)
        pool_size = min(total_needed, total_instances)
        pool = rng.sample(all_indices, pool_size)
        if total_needed > total_instances:
            weights = [m.get("weight", 1.0) * m["_count"] for m in members]
            total_w = sum(weights)
            allocated: list[int] = []
            for j, m in enumerate(members):
                share = round(total_instances * weights[j] / total_w) if total_w else 0
                allocated.append(share)
            diff = total_instances - sum(allocated)
            if diff != 0:
                allocated[0] += diff
            pos = 0
            for m, alloc in zip(members, allocated):
                result[m["label"]] = set(pool[pos : pos + alloc])
                pos += alloc
        else:
            pos = 0
            for m in members:
                c = m["_count"]
                result[m["label"]] = set(pool[pos : pos + c])
                pos += c

    for og in independent:
        c = og["_count"]
        result[og["label"]] = set(rng.sample(all_indices, c))

    return result


def activate_optional_groups(
    flow_dict: dict,
    preselected: set[str],
) -> set[str]:
    """Filter pre-selected groups by applicability for a single instance.

    Called per-instance after ``preselect_edge_cases`` has determined the
    global assignment.  Returns the subset of *preselected* labels that
    pass applicability on this instance's steps.
    """
    steps = flow_dict.get("steps", [])
    return {
        og["label"]
        for og in flow_dict.get("optional_groups", [])
        if og["label"] in preselected and _is_applicable(og, steps)
    }


_MONEY_MOVEMENT_TYPES: frozenset[str] = frozenset(
    {
        "payment_order",
        "incoming_payment_detail",
        "expected_payment",
        "ledger_transaction",
    }
)


def mark_staged(flow_dict: dict) -> dict:
    """Mark money-movement steps as staged: true."""
    for step in flow_dict.get("steps", []):
        if step.get("type") in _MONEY_MOVEMENT_TYPES:
            step["staged"] = True
    return flow_dict


def _select_from_pool(
    selection: str,
    count: int,
    total: int,
    edge_selections: dict[str, set[int]] | None,
) -> set[int]:
    """Return up to *count* instance indices for a single staging pool."""
    if count <= 0:
        return set()
    edge_sel = edge_selections or {}

    if selection == "all":
        pool = list(range(total))
    elif selection == "happy_path":
        edge_indices: set[int] = set()
        for indices in edge_sel.values():
            edge_indices |= indices
        pool = [i for i in range(total) if i not in edge_indices]
    else:
        pool = sorted(edge_sel.get(selection, set()))

    n = min(count, len(pool))
    return set(pool[:n]) if n > 0 else set()


def select_staged_instances(
    recipe: GenerationRecipeV1,
    total: int,
    rng: random.Random,
    edge_selections: dict[str, set[int]] | None = None,
) -> set[int]:
    """Return the set of instance indices to stage.

    Evaluates each ``StagingRule`` independently and unions the results.
    Legacy ``staged_count``/``staged_selection`` fields are promoted into
    ``staging_rules`` by the model validator so they work identically.
    """
    if not recipe.staging_rules:
        return set()

    result: set[int] = set()
    for rule in recipe.staging_rules:
        result |= _select_from_pool(
            rule.selection,
            rule.count,
            total,
            edge_selections,
        )
    return result


def _apply_payment_mix(flow_dict: dict, mix: PaymentMixConfig) -> dict:
    """Remove steps whose resource types are excluded by the payment mix."""
    excluded: set[str] = set()
    for flag_name, resource_type in PAYMENT_MIX_TYPE_MAP.items():
        if not getattr(mix, flag_name):
            excluded.add(resource_type)
    if not excluded:
        return flow_dict
    steps = flow_dict.get("steps") or []
    removed_ids = {s["step_id"] for s in steps if s.get("type") in excluded}
    flow_dict["steps"] = [s for s in steps if s.get("type") not in excluded]
    for s in flow_dict["steps"]:
        if "depends_on" in s:
            s["depends_on"] = [d for d in s["depends_on"] if d not in removed_ids]
    return flow_dict


def _build_actor_profile_caches(
    pattern: FundsFlowConfig,
    recipe: GenerationRecipeV1,
) -> dict[str, tuple[list[dict], list[dict]]]:
    """Pre-generate seed profiles for actors with non-global datasets."""
    caches: dict[str, tuple[list[dict], list[dict]]] = {}
    for alias, frame in pattern.actors.items():
        override = recipe.actor_overrides.get(alias)
        effective_ds = (
            override.dataset if override and override.dataset else None
        ) or frame.dataset
        if effective_ds:
            biz, indiv = seed_loader.generate_profiles(
                effective_ds,
                recipe.instances,
                recipe.seed,
            )
            caches[alias] = (biz, indiv)
    return caches


def _enrich_profile_with_actors(
    profile: dict[str, str],
    pattern: FundsFlowConfig,
    recipe: GenerationRecipeV1,
    actor_caches: dict[str, tuple[list[dict], list[dict]]],
    global_biz: list[dict],
    global_indiv: list[dict],
    instance: int,
) -> dict[str, str]:
    """Add per-actor name keys ({alias}_name, {alias}_business_name, ...) to the profile."""
    safe = defaultdict(str)
    for alias, frame in pattern.actors.items():
        override = recipe.actor_overrides.get(alias)
        recipe_cn = override.customer_name if override and override.customer_name else None
        literal_name = recipe_cn or frame.customer_name
        if literal_name:
            profile[f"{alias}_name"] = literal_name
            continue

        if alias in actor_caches:
            a_biz, a_indiv = actor_caches[alias]
            actor_profile = seed_loader.pick_profile(a_biz, a_indiv, instance)
        else:
            actor_profile = seed_loader.pick_profile(global_biz, global_indiv, instance)

        name_tpl = (
            override.name_template if override and override.name_template else None
        ) or frame.name_template

        entity_type = override.entity_type if override and override.entity_type else None

        if name_tpl:
            safe.update(actor_profile)
            try:
                rendered = name_tpl.format_map(safe)
            except (ValueError, KeyError):
                rendered = actor_profile.get("business_name", "")
        elif entity_type == "individual":
            first = actor_profile.get("first_name", "")
            last = actor_profile.get("last_name", "")
            rendered = f"{first} {last}".strip() or actor_profile.get("business_name", "")
        else:
            rendered = actor_profile.get("business_name", "")

        profile[f"{alias}_name"] = rendered
        for k, v in actor_profile.items():
            profile[f"{alias}_{k}"] = v

    return profile


def generate_from_recipe(
    recipe: GenerationRecipeV1,
    base_config: DataLoaderConfig,
) -> GenerationResult:
    """Expand a recipe into N flow instances, compile, and render Mermaid.

    Returns a ``GenerationResult`` with compiled config, diagrams,
    edge-case map, flow IRs, and expanded flow configs.
    """
    pattern = next(
        (f for f in base_config.funds_flows if f.ref == recipe.flow_ref),
        None,
    )
    if pattern is None:
        available = [f.ref for f in base_config.funds_flows]
        raise ValueError(
            f"flow_ref '{recipe.flow_ref}' not found in loaded config. Available: {available}"
        )

    biz_ds = recipe.business_dataset or recipe.seed_dataset
    indiv_ds = recipe.individual_dataset or recipe.seed_dataset
    biz_profiles, _ = seed_loader.generate_profiles(
        biz_ds,
        recipe.instances,
        recipe.seed,
    )
    _, indiv_profiles = seed_loader.generate_profiles(
        indiv_ds,
        recipe.instances,
        recipe.seed,
    )

    actor_caches = _build_actor_profile_caches(pattern, recipe)

    edge_overrides = (
        {k: v.model_dump() for k, v in recipe.edge_case_overrides.items()}
        if recipe.edge_case_overrides
        else None
    )
    pattern_dict = pattern.model_dump()
    edge_selections = preselect_edge_cases(
        pattern_dict,
        recipe.edge_case_count,
        recipe.instances,
        recipe.seed,
        overrides=edge_overrides,
    )

    staged_instances = select_staged_instances(
        recipe,
        recipe.instances,
        random.Random(recipe.seed),
        edge_selections=edge_selections,
    )

    # Pre-compute timing spread offsets (deterministic for given seed)
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

    extra_resources: dict[str, list[dict]] = {}
    flows: list[FundsFlowConfig] = []
    for i in range(recipe.instances):
        rng = random.Random(recipe.seed + i)
        profile = seed_loader.pick_profile(biz_profiles, indiv_profiles, i)
        profile = _enrich_profile_with_actors(
            profile,
            pattern,
            recipe,
            actor_caches,
            biz_profiles,
            indiv_profiles,
            i,
        )
        flow_dict, instance_resources = clone_flow(pattern, i, profile)

        if instance_resources:
            expanded = _expand_instance_resources(instance_resources, i, profile)
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

        # Apply timing/seasoning (stamps effective_date / effective_at)
        # Only stamp dates when there is an explicit timing signal:
        #   - start_date anchor, spread across days, step offsets, or
        #     flow-level timing config.
        # Without these, POs are created without effective_date so they
        # process immediately.
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
            # Remove internal keys before validation
            flow_dict.pop("_computed_dates", None)
            flow_dict.pop("_base_date", None)

        flows.append(FundsFlowConfig.model_validate(flow_dict))

    flow_irs = compile_flows(flows, base_config)
    compiled = emit_dataloader_config(
        flow_irs,
        base_config=base_config,
        extra_resources=extra_resources,
    )

    diagrams: list[str] = []
    for ir, flow_config in zip(flow_irs[:10], flows[:10]):
        diagrams.append(render_mermaid(ir, flow_config))

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
