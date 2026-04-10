"""Generation pipeline: clone, variance, activation, staging, recipe expansion.

Transforms a ``GenerationRecipeV1`` into N flow instances plus compiled output.
The ordered phases below match ``generate_from_recipe`` (verified 2026-04-10;
keep in sync when refactoring — Plan 08 Track B).

**Per-instance loop** runs for ``i in range(recipe.instances)``.

+----------+--------------------------------------------------------------+
| Phase    | What runs                                                    |
+==========+==============================================================+
| P0       | Resolve pattern: find ``FundsFlowConfig`` by ``flow_ref``;   |
|          | build ``pattern_dict``, ``edge_overrides``.                  |
+----------+--------------------------------------------------------------+
| P1       | Edge preselection: ``preselect_edge_cases``.                 |
+----------+--------------------------------------------------------------+
| P2       | Staging: ``select_staged_instances``.                        |
+----------+--------------------------------------------------------------+
| P3       | Timing precompute: ``compute_spread_offsets`` when spread > 0.|
+----------+--------------------------------------------------------------+
| P4       | Per ``i``: profile → ``clone_flow`` → ``_expand_instance_resources``. |
+----------+--------------------------------------------------------------+
| P5       | Per ``i``: ``apply_overrides`` (recipe-level).               |
+----------+--------------------------------------------------------------+
| P6       | Per ``i``: ``apply_amount_variance`` (optional).             |
+----------+--------------------------------------------------------------+
| P7       | Per ``i``: optional groups — ``activate_optional_groups``,   |
|          | stamp ``_flow_*`` metadata, ``flatten_optional_groups``.     |
+----------+--------------------------------------------------------------+
| P8       | Per ``i``: ``mark_staged`` when ``i`` is staged.           |
+----------+--------------------------------------------------------------+
| P9       | Per ``i``: ``_apply_payment_mix``.                           |
+----------+--------------------------------------------------------------+
| P10      | Per ``i``: ``compute_effective_dates`` when timing signals.  |
+----------+--------------------------------------------------------------+
| P11      | Per ``i``: ``FundsFlowConfig.model_validate(flow_dict)``.    |
+----------+--------------------------------------------------------------+
| P12      | Post-loop: ``compile_flows``, ``emit_dataloader_config``.    |
+----------+--------------------------------------------------------------+
| P13      | Post-loop: ``render_mermaid`` for first 10 ``(ir, flow)`` pairs. |
+----------+--------------------------------------------------------------+

**Order note:** overrides run before variance and before optional-group
activation; ``flatten_optional_groups`` runs after activation and metadata
stamping. Mermaid runs after the full multi-instance compile, not inside the
instance loop.
"""

from __future__ import annotations

import copy
import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from models import (
    PAYMENT_MIX_TYPE_MAP,
    DataLoaderConfig,
    FundsFlowConfig,
    GenerationRecipeV1,
    PaymentMixConfig,
)

from . import seed_loader
from .ir import FlowIR


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


def _bind_bare_business_name(tpl: dict, row: dict[str, str], pattern: FundsFlowConfig) -> None:
    """Bare ``{business_name}`` in a row: bind to the user LE whose entity_ref stem matches ``ref``."""
    keys = ("business_name", "name", "party_name")
    if not any(
        isinstance(tpl.get(k), str) and "{business_name}" in tpl[k] and "{user_" not in tpl[k]
        for k in keys
    ):
        return
    ref = tpl.get("ref")
    if not isinstance(ref, str) or not ref:
        return
    stem = ref.split("{", 1)[0].rstrip("_")
    if not stem:
        return
    for alias, frame in pattern.actors.items():
        if frame.frame_type != "user":
            continue
        er = frame.entity_ref or ""
        if "legal_entity." not in er:
            continue
        tail = er.split("legal_entity.", 1)[1].split("{", 1)[0].rstrip("_")
        if tail == stem:
            bn = row.get(f"{alias}_business_name") or row.get(f"{alias}_name")
            if bn:
                row["business_name"] = bn
            return


def _expand_instance_resources(
    instance_resources: dict[str, list[dict]],
    instance: int,
    profile: dict[str, str],
    pattern: FundsFlowConfig | None = None,
) -> dict[str, list[dict]]:
    """Clone instance_resources templates with profile substitution."""
    base_mapping = {"instance": f"{instance:04d}", **profile}
    result: dict[str, list[dict]] = {}
    for section, templates in instance_resources.items():
        section_items: list[dict] = []
        for tpl in templates:
            cloned = copy.deepcopy(tpl)
            row = dict(base_mapping)
            if pattern is not None:
                _bind_bare_business_name(cloned, row, pattern)
            cloned = deep_format_map(cloned, row)
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


def _build_instance_profile(
    pattern: FundsFlowConfig,
    recipe: GenerationRecipeV1,
    instance: int,
) -> dict[str, str]:
    """Per-instance profile: actor-prefixed keys plus legacy top-level name fields."""
    biz_ds = recipe.business_dataset or recipe.seed_dataset
    indiv_ds = recipe.individual_dataset or recipe.seed_dataset
    if not pattern.actors:
        sub = seed_loader.actor_subseed(recipe.seed, pattern.ref, "_", instance)
        return seed_loader.profile_for_split_biz_indiv(biz_ds, indiv_ds, sub)

    profile: dict[str, str] = {}
    safe = defaultdict(str)

    for alias, frame in pattern.actors.items():
        override = recipe.actor_overrides.get(alias)
        recipe_cn = override.customer_name if override and override.customer_name else None
        literal_name = recipe_cn or frame.customer_name
        if literal_name:
            profile[f"{alias}_name"] = literal_name
            profile[f"{alias}_business_name"] = literal_name
            continue

        effective_ds = (
            override.dataset if override and override.dataset else None
        ) or frame.dataset
        sub = seed_loader.actor_subseed(recipe.seed, pattern.ref, alias, instance)
        if effective_ds:
            actor_profile = seed_loader.profile_for(effective_ds, sub)
        else:
            actor_profile = seed_loader.profile_for_split_biz_indiv(biz_ds, indiv_ds, sub)

        name_tpl = (
            override.name_template if override and override.name_template else None
        ) or frame.name_template
        entity_type = override.entity_type if override and override.entity_type else None

        if name_tpl:
            safe.clear()
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

    for alias in pattern.actors:
        if f"{alias}_business_name" in profile:
            profile.setdefault("business_name", profile[f"{alias}_business_name"])
            profile.setdefault("first_name", profile.get(f"{alias}_first_name", ""))
            profile.setdefault("last_name", profile.get(f"{alias}_last_name", ""))
            profile.setdefault("industry", profile.get(f"{alias}_industry", ""))
            profile.setdefault("country", profile.get(f"{alias}_country", "US"))
            break

    return profile


def generate_from_recipe(
    recipe: GenerationRecipeV1,
    base_config: DataLoaderConfig,
) -> GenerationResult:
    """Expand a recipe into N flow instances, compile, and render Mermaid.

    Phase order is documented in this module's docstring (P0–P13).  The
    orchestration body lives in ``generation_pipeline.run_generation_pipeline``.

    Returns a ``GenerationResult`` with compiled config, diagrams,
    edge-case map, flow IRs, and expanded flow configs.
    """
    from .generation_pipeline import run_generation_pipeline

    return run_generation_pipeline(recipe, base_config)
