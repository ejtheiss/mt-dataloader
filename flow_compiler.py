"""Funds Flow DSL compiler.

Pipeline: FundsFlowConfig[] → resolve_actors → compile_flows → FlowIR[]
          → emit_dataloader_config → DataLoaderConfig

All compilation logic lives in this single module. The engine, handlers,
and main.py are unchanged — the emitted DataLoaderConfig feeds directly
into the existing pipeline.
"""

from __future__ import annotations

import copy
import random
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from models import (
    DataLoaderConfig,
    FundsFlowConfig,
    FundsFlowStepConfig,
    GenerationRecipeV1,
    OptionalGroupConfig,
    PaymentMixConfig,
)

import seed_loader

__all__ = [
    "maybe_compile",
    "flatten_optional_groups",
    "render_mermaid",
    "clone_flow",
    "apply_overrides",
    "apply_amount_variance",
    "activate_optional_groups",
    "mark_staged",
    "select_staged_instances",
    "generate_from_recipe",
    "actor_display_name",
    "compute_flow_status",
    "flow_account_deltas",
    "compile_diagnostics",
    "deep_format_map",
]

# ---------------------------------------------------------------------------
# Resource type → DataLoaderConfig section name
# ---------------------------------------------------------------------------

_RESOURCE_TYPE_TO_SECTION: dict[str, str] = {
    "payment_order": "payment_orders",
    "incoming_payment_detail": "incoming_payment_details",
    "ledger_transaction": "ledger_transactions",
    "expected_payment": "expected_payments",
    "return": "returns",
    "reversal": "reversals",
    "transition_ledger_transaction": "transition_ledger_transactions",
}

_NEEDS_PAYMENT_TYPE: frozenset[str] = frozenset({
    "incoming_payment_detail",
    "payment_order",
})

# ---------------------------------------------------------------------------
# FlowIR dataclasses (internal — not Pydantic)
# ---------------------------------------------------------------------------


@dataclass
class LedgerGroup:
    """One set of ledger entries that emits as a standalone LT or inline LT."""

    group_id: str
    inline: bool
    entries: list[dict]
    metadata: dict[str, str]
    status: str | None = None


@dataclass
class FlowIRStep:
    """One step in the FlowIR — compiles to one resource in DataLoaderConfig."""

    step_id: str
    flow_ref: str
    instance_id: str
    depends_on: list[str]
    resource_type: str
    payload: dict
    ledger_groups: list[LedgerGroup]
    trace_metadata: dict[str, str]
    optional_group: str | None = None

    @property
    def emitted_ref(self) -> str:
        return f"{self.flow_ref}__{self.instance_id}__{self.step_id}"


@dataclass
class FlowIR:
    """Complete IR for one flow instance."""

    flow_ref: str
    instance_id: str
    pattern_type: str
    trace_key: str
    trace_value: str
    trace_metadata: dict[str, str]
    steps: list[FlowIRStep] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_ref_segment(segment: str) -> None:
    if "__" in segment:
        raise ValueError(
            f"Ref segment '{segment}' must not contain '__' "
            f"(reserved as instance separator)"
        )


def resolve_actors(obj: Any, actors: dict[str, str]) -> Any:
    """Replace ``@actor:<alias>`` references with concrete ``$ref:`` values."""
    if isinstance(obj, str) and obj.startswith("@actor:"):
        alias = obj[7:]
        if alias not in actors:
            raise ValueError(
                f"Unknown actor alias '{alias}' — "
                f"available: {sorted(actors.keys())}"
            )
        return actors[alias]
    if isinstance(obj, dict):
        return {k: resolve_actors(v, actors) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_actors(v, actors) for v in obj]
    return obj


def expand_trace_value(template: str, ref: str, instance: int) -> str:
    try:
        return template.format_map({"ref": ref, "instance": instance})
    except KeyError as e:
        raise ValueError(
            f"Unknown placeholder {e} in trace_value_template '{template}'"
        ) from e


def _auto_derive_lifecycle_refs(
    step: FundsFlowStepConfig,
    step_dict: dict,
    step_ref_map: dict[str, str],
    all_steps: list[FundsFlowStepConfig],
) -> None:
    """Auto-set returnable_id / payment_order_id / ledger_transaction_id from depends_on targets."""
    if step.type == "return" and "returnable_id" not in step_dict:
        for dep_id in step.depends_on:
            dep_step = next((s for s in all_steps if s.step_id == dep_id), None)
            if dep_step is None:
                continue
            if dep_step.type == "incoming_payment_detail":
                step_dict["returnable_id"] = step_ref_map[dep_id]
                break

    if step.type == "reversal" and "payment_order_id" not in step_dict:
        for dep_id in step.depends_on:
            dep_step = next((s for s in all_steps if s.step_id == dep_id), None)
            if dep_step is None:
                continue
            if dep_step.type == "payment_order":
                step_dict["payment_order_id"] = step_ref_map[dep_id]
                break

    if step.type == "transition_ledger_transaction" and "ledger_transaction_id" not in step_dict:
        for dep_id in step.depends_on:
            dep_step = next((s for s in all_steps if s.step_id == dep_id), None)
            if dep_step is None:
                continue
            if dep_step.type == "ledger_transaction":
                step_dict["ledger_transaction_id"] = step_ref_map[dep_id]
                break
            if dep_step.type in ("payment_order", "expected_payment", "return", "reversal"):
                if dep_step.ledger_entries and getattr(dep_step, "ledger_inline", False):
                    parent_ref = step_ref_map[dep_id]
                    step_dict["ledger_transaction_id"] = f"{parent_ref}.ledger_transaction"
                    break


def _inject_lifecycle_depends_on(step: FlowIRStep) -> None:
    """Add depends_on edges for lifecycle ordering the engine can't infer
    from data refs alone."""
    if step.resource_type == "return":
        ipd_ref = step.payload.get("returnable_id", "")
        if ipd_ref.startswith("$ref:") and ipd_ref not in step.depends_on:
            step.depends_on.append(ipd_ref)
    elif step.resource_type == "reversal":
        po_ref = step.payload.get("payment_order_id", "")
        if po_ref.startswith("$ref:") and po_ref not in step.depends_on:
            step.depends_on.append(po_ref)
    elif step.resource_type == "transition_ledger_transaction":
        lt_ref = step.payload.get("ledger_transaction_id", "")
        if lt_ref.startswith("$ref:") and lt_ref not in step.depends_on:
            step.depends_on.append(lt_ref)


# ---------------------------------------------------------------------------
# Optional group flattening
# ---------------------------------------------------------------------------


def flatten_optional_groups(
    flow_dict: dict, activated_groups: set[str] | None = None
) -> dict:
    """Merge activated optional group steps into the main steps list.

    If activated_groups is None, ALL groups are included
    (for preview/documentation rendering). If it's an empty set,
    none are included (happy-path only).

    Mutates and returns flow_dict. Removes the optional_groups key.
    """
    optional_groups = flow_dict.pop("optional_groups", [])
    for og in optional_groups:
        if activated_groups is None or og["label"] in activated_groups:
            flow_dict.setdefault("steps", []).extend(og["steps"])
    return flow_dict


# ---------------------------------------------------------------------------
# Generation pipeline: clone, variance, activation, staging
# ---------------------------------------------------------------------------


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
    """Clone instance_resources templates with profile substitution.

    Returns {section_name: [resource_dict, ...]} ready to merge.
    """
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
    """Clone a flow for a specific instance with optional profile substitution.

    Returns (flow_dict, instance_resources_or_None).
    """
    as_dict = flow.model_dump()
    as_dict["ref"] = f"{flow.ref}__{instance:04d}"

    ir = as_dict.pop("instance_resources", None)

    if profile:
        mapping = {"instance": f"{instance:04d}", "ref": as_dict["ref"], **profile}
        as_dict = deep_format_map(as_dict, mapping)

    return as_dict, ir


def apply_overrides(flow_dict: dict, overrides: dict[str, Any]) -> dict:
    """Apply dotted-path key-value overrides to a flow dict.

    Supports numeric list indices: ``steps.0.amount`` → flow_dict["steps"][0]["amount"]
    """
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
    flow_dict: dict, variance_pct: float, rng: random.Random
) -> dict:
    """Apply +/- variance_pct jitter to all amount fields in steps.

    Uses the SAME jitter factor for all amounts within a single step
    to keep ledger entries balanced (DR and CR get the same percentage).
    """
    if variance_pct <= 0:
        return flow_dict
    factor = variance_pct / 100.0
    all_steps = list(flow_dict.get("steps") or [])
    for og in flow_dict.get("optional_groups") or []:
        all_steps.extend(og.get("steps") or [])
    for step in all_steps:
        entries = step.get("ledger_entries") or []
        if not step.get("amount") and not entries:
            continue
        jitter = rng.uniform(-factor, factor)
        if "amount" in step and isinstance(step["amount"], (int, float)):
            step["amount"] = max(1, round(step["amount"] * (1 + jitter)))
        for entry in entries:
            if "amount" in entry and isinstance(entry["amount"], (int, float)):
                entry["amount"] = max(1, round(entry["amount"] * (1 + jitter)))
    return flow_dict


def activate_optional_groups(
    flow_dict: dict, frequency: float, rng: random.Random
) -> set[str]:
    """Determine which optional groups to activate for this instance.

    Returns the set of group labels to include.
    """
    if frequency <= 0:
        return set()
    activated: set[str] = set()
    for og in flow_dict.get("optional_groups", []):
        if rng.random() < frequency:
            activated.add(og["label"])
    return activated


_MONEY_MOVEMENT_TYPES: frozenset[str] = frozenset({
    "payment_order", "incoming_payment_detail",
    "expected_payment", "ledger_transaction",
})


def mark_staged(flow_dict: dict) -> dict:
    """Mark money-movement steps as staged: true."""
    for step in flow_dict.get("steps", []):
        if step.get("type") in _MONEY_MOVEMENT_TYPES:
            step["staged"] = True
    return flow_dict


def select_staged_instances(
    recipe: GenerationRecipeV1, total: int, rng: random.Random
) -> set[int]:
    """Return the set of instance indices to stage."""
    if recipe.staged_count <= 0:
        return set()
    count = min(recipe.staged_count, total)
    if recipe.staged_selection == "first":
        return set(range(count))
    else:
        return set(rng.sample(range(total), count))


_PAYMENT_MIX_TYPE_MAP: dict[str, str] = {
    "include_expected_payments": "expected_payment",
    "include_payment_orders": "payment_order",
    "include_ipds": "incoming_payment_detail",
    "include_returns": "return",
    "include_reversals": "reversal",
    "include_standalone_lts": "ledger_transaction",
}


def _apply_payment_mix(flow_dict: dict, mix: PaymentMixConfig) -> dict:
    """Remove steps whose resource types are excluded by the payment mix."""
    excluded: set[str] = set()
    for flag_name, resource_type in _PAYMENT_MIX_TYPE_MAP.items():
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


def generate_from_recipe(
    recipe: GenerationRecipeV1,
    base_config: DataLoaderConfig,
) -> tuple[DataLoaderConfig, list[str]]:
    """Expand a recipe into N flow instances, compile, and render Mermaid.

    Returns (compiled_config, mermaid_diagrams).
    """
    pattern = next(
        (f for f in base_config.funds_flows if f.ref == recipe.flow_ref),
        None,
    )
    if pattern is None:
        available = [f.ref for f in base_config.funds_flows]
        raise ValueError(
            f"flow_ref '{recipe.flow_ref}' not found in loaded config. "
            f"Available: {available}"
        )

    biz_ds = recipe.business_dataset or recipe.seed_dataset
    indiv_ds = recipe.individual_dataset or recipe.seed_dataset
    biz_profiles, _ = seed_loader.generate_profiles(
        biz_ds, recipe.instances, recipe.seed,
    )
    _, indiv_profiles = seed_loader.generate_profiles(
        indiv_ds, recipe.instances, recipe.seed,
    )

    staged_instances = select_staged_instances(
        recipe, recipe.instances, random.Random(recipe.seed)
    )

    extra_resources: dict[str, list[dict]] = {}
    flows: list[FundsFlowConfig] = []
    for i in range(recipe.instances):
        rng = random.Random(recipe.seed + i)
        profile = seed_loader.pick_profile(biz_profiles, indiv_profiles, i)
        flow_dict, instance_resources = clone_flow(pattern, i, profile)

        if instance_resources:
            expanded = _expand_instance_resources(instance_resources, i, profile)
            for section, items in expanded.items():
                extra_resources.setdefault(section, []).extend(items)

        if recipe.overrides:
            apply_overrides(flow_dict, recipe.overrides)

        if recipe.amount_variance_pct > 0:
            apply_amount_variance(flow_dict, recipe.amount_variance_pct, rng)

        activated = activate_optional_groups(
            flow_dict, recipe.edge_case_frequency, rng
        )

        for og in flow_dict.get("optional_groups", []):
            if og["label"] in activated:
                for step in og["steps"]:
                    step.setdefault("metadata", {})
                    step["metadata"]["_flow_optional_group"] = og["label"]
                    step["metadata"]["_flow_edge_case_frequency"] = str(recipe.edge_case_frequency)
                    step["metadata"]["_flow_trigger"] = og.get("trigger", "manual")

        flatten_optional_groups(flow_dict, activated)

        if i in staged_instances:
            mark_staged(flow_dict)

        if recipe.payment_mix:
            _apply_payment_mix(flow_dict, recipe.payment_mix)

        flows.append(FundsFlowConfig.model_validate(flow_dict))

    flow_irs = compile_flows(flows, base_config)
    compiled = emit_dataloader_config(
        flow_irs, base_config=base_config, extra_resources=extra_resources,
    )

    diagrams: list[str] = []
    for ir, flow_config in zip(flow_irs[:10], flows[:10]):
        diagrams.append(render_mermaid(ir, flow_config))

    return compiled, diagrams


# ---------------------------------------------------------------------------
# Compiler: DSL → FlowIR
# ---------------------------------------------------------------------------


def compile_flows(
    flows: list[FundsFlowConfig],
    base_config: DataLoaderConfig,
) -> list[FlowIR]:
    """Compile FundsFlowConfig DSL entries into FlowIR instances."""
    result: list[FlowIR] = []

    for flow in flows:
        parts = flow.ref.rsplit("__", 1)
        if len(parts) == 2 and parts[1].isdigit():
            _validate_ref_segment(parts[0])
            instance_id = parts[1]
        else:
            _validate_ref_segment(flow.ref)
            instance_id = "0000"
        trace_value = expand_trace_value(
            flow.trace_value_template, flow.ref, 0
        )
        trace_meta = {flow.trace_key: trace_value, **flow.trace_metadata}

        og_step_ids: dict[str, str] = {}
        for og in flow.optional_groups:
            for s in og.steps:
                og_step_ids[s.step_id] = og.label
        # Also pick up from metadata if steps were already flattened
        # (generation pipeline stamps _flow_optional_group before flatten)
        for s in flow.steps:
            meta = getattr(s, "metadata", None) or {}
            if "_flow_optional_group" in meta and s.step_id not in og_step_ids:
                og_step_ids[s.step_id] = meta["_flow_optional_group"]

        # --- Pass 1: build ref map for ALL steps ---
        step_ref_map: dict[str, str] = {}
        for step in flow.steps:
            _validate_ref_segment(step.step_id)
            emitted_ref = f"{flow.ref}__{instance_id}__{step.step_id}"
            typed_ref = f"$ref:{step.type}.{emitted_ref}"
            step_ref_map[step.step_id] = typed_ref

        # --- Pass 2: process step payloads ---
        ir_steps: list[FlowIRStep] = []
        for step in flow.steps:
            emitted_ref = f"{flow.ref}__{instance_id}__{step.step_id}"

            step_dict = step.model_dump(
                exclude={"step_id", "type", "depends_on", "ledger_entries",
                         "ledger_status", "ledger_inline"},
                exclude_none=True,
            )

            if "payment_type" in step_dict:
                step_dict["type"] = step_dict.pop("payment_type")
            elif step.type in _NEEDS_PAYMENT_TYPE:
                raise ValueError(
                    f"Step '{step.step_id}' (type={step.type}) requires "
                    f"'payment_type' (e.g., 'ach', 'wire'). The DSL 'type' "
                    f"field is the resource type; use 'payment_type' for the "
                    f"payment method."
                )

            step_dict = resolve_actors(step_dict, flow.actors)

            step_dict["metadata"] = {
                **step_dict.get("metadata", {}),
                **trace_meta,
            }

            # depends_on: direct index — Pydantic already validated targets
            ir_depends = [step_ref_map[dep] for dep in step.depends_on]

            # Auto-derive lifecycle data-field refs
            _auto_derive_lifecycle_refs(
                step, step_dict, step_ref_map, flow.steps
            )

            ledger_groups: list[LedgerGroup] = []
            if step.ledger_entries:
                entries_resolved = resolve_actors(
                    [e.model_dump(exclude_none=True) for e in step.ledger_entries],
                    flow.actors,
                )
                ledger_groups.append(LedgerGroup(
                    group_id=f"{step.step_id}_lg0",
                    inline=step.ledger_inline,
                    entries=entries_resolved,
                    metadata=trace_meta.copy(),
                    status=step.ledger_status,
                ))

            ir_steps.append(FlowIRStep(
                step_id=step.step_id,
                flow_ref=flow.ref,
                instance_id=instance_id,
                depends_on=ir_depends,
                resource_type=step.type,
                payload=step_dict,
                ledger_groups=ledger_groups,
                trace_metadata=trace_meta,
                optional_group=og_step_ids.get(step.step_id),
            ))

        result.append(FlowIR(
            flow_ref=flow.ref,
            instance_id=instance_id,
            pattern_type=flow.pattern_type,
            trace_key=flow.trace_key,
            trace_value=trace_value,
            trace_metadata=trace_meta,
            steps=ir_steps,
        ))

    return result


# ---------------------------------------------------------------------------
# Emitter: FlowIR → DataLoaderConfig
# ---------------------------------------------------------------------------


def emit_dataloader_config(
    flow_irs: list[FlowIR],
    base_config: DataLoaderConfig,
    extra_resources: dict[str, list[dict]] | None = None,
) -> DataLoaderConfig:
    """Emit FlowIR steps into DataLoaderConfig resource sections.

    extra_resources is keyed by section name (e.g. "legal_entities")
    and merged before flow steps so instance infrastructure is available
    for ref resolution.

    The emitted config passes through ``DataLoaderConfig.model_validate()``
    which runs every existing Pydantic validator as a safety net.
    """
    data = base_config.model_dump(exclude_none=True)
    data["funds_flows"] = []

    if extra_resources:
        for section, items in extra_resources.items():
            existing = data.get(section, [])
            existing_refs = {item.get("ref") for item in existing if isinstance(item, dict)}
            for item in items:
                if isinstance(item, dict) and item.get("ref") in existing_refs:
                    continue
                existing.append(item)
                if isinstance(item, dict) and item.get("ref"):
                    existing_refs.add(item["ref"])
            data[section] = existing

    for flow_ir in flow_irs:
        for step in flow_ir.steps:
            _inject_lifecycle_depends_on(step)

            ref = step.emitted_ref
            resource_type = step.resource_type
            section = _RESOURCE_TYPE_TO_SECTION[resource_type]

            resource_dict: dict[str, Any] = {
                "ref": ref,
                **step.payload,
            }
            if resource_type == "transition_ledger_transaction":
                resource_dict.pop("description", None)
            if step.depends_on:
                resource_dict["depends_on"] = step.depends_on

            for lg in step.ledger_groups:
                if not lg.inline:
                    if resource_type == "ledger_transaction":
                        resource_dict["ledger_entries"] = lg.entries
                        resource_dict["metadata"] = {
                            **resource_dict.get("metadata", {}),
                            **lg.metadata,
                        }
                        if lg.status:
                            resource_dict["status"] = lg.status
                    else:
                        lt_ref = f"{ref}__{lg.group_id}"
                        lt_dict: dict[str, Any] = {
                            "ref": lt_ref,
                            "ledger_entries": lg.entries,
                            "metadata": lg.metadata,
                            "depends_on": [f"$ref:{resource_type}.{ref}"],
                        }
                        if step.payload.get("description"):
                            lt_dict["description"] = step.payload["description"]
                        if lg.status:
                            lt_dict["status"] = lg.status
                        data.setdefault("ledger_transactions", []).append(lt_dict)
                else:
                    inline_lt: dict[str, Any] = {
                        "ledger_entries": lg.entries,
                        "metadata": lg.metadata,
                    }
                    if step.payload.get("description"):
                        inline_lt["description"] = step.payload["description"]
                    if lg.status:
                        inline_lt["status"] = lg.status
                    resource_dict["ledger_transaction"] = inline_lt

            data.setdefault(section, []).append(resource_dict)

    return DataLoaderConfig.model_validate(data)


# ---------------------------------------------------------------------------
# Mermaid sequence diagram renderer
# ---------------------------------------------------------------------------

_DIR_ABBREV: dict[str, str] = {"debit": "DR", "credit": "CR"}

_ARROW_BY_TYPE: dict[str, str] = {
    "incoming_payment_detail": "-)",
    "payment_order": "-)",
    "ledger_transaction": "->>",
    "expected_payment": "-)",
    "return": "--x",
    "reversal": "--x",
    "transition_ledger_transaction": "-->>",
}


def actor_display_name(ref_value: str) -> str:
    """``$ref:internal_account.ops_usd`` → ``Ops Usd``."""
    parts = ref_value.replace("$ref:", "").split(".")
    if len(parts) > 1:
        return parts[1].replace("_", " ").title()
    return parts[0].replace("_", " ").title()


def _resolve_step_participants(
    step: FlowIRStep,
    actors: dict[str, str],
) -> tuple[str, str]:
    """Return (source, destination) display names for a sequence arrow."""
    payload = step.payload
    rtype = step.resource_type

    if rtype == "incoming_payment_detail":
        dest_ref = payload.get("internal_account_id", "")
        dest = actor_display_name(dest_ref) if dest_ref else "Internal"
        return ("External", dest)

    if rtype == "payment_order":
        src_ref = payload.get("originating_account_id", "")
        src = actor_display_name(src_ref) if src_ref else "Internal"
        return (src, "External")

    if rtype in ("return", "reversal"):
        dest_ref = payload.get("internal_account_id", "")
        dest = actor_display_name(dest_ref) if dest_ref else "Internal"
        return (dest, "External")

    if rtype == "ledger_transaction":
        if step.ledger_groups:
            entries = step.ledger_groups[0].entries
            debit_acct = next(
                (e.get("ledger_account_id", "") for e in entries if e.get("direction") == "debit"),
                "",
            )
            credit_acct = next(
                (e.get("ledger_account_id", "") for e in entries if e.get("direction") == "credit"),
                "",
            )
            src = actor_display_name(debit_acct) if debit_acct else "Debit"
            dest = actor_display_name(credit_acct) if credit_acct else "Credit"
            return (src, dest)
        return ("Ledger", "Ledger")

    if rtype == "expected_payment":
        ia_ref = payload.get("internal_account_id", "")
        dest = actor_display_name(ia_ref) if ia_ref else "Internal"
        return ("External", dest)

    if rtype == "transition_ledger_transaction":
        return ("Ledger", "Ledger")

    return ("System", "System")


def render_mermaid(
    flow_ir: FlowIR,
    flow_config: FundsFlowConfig | None = None,
    *,
    show_amounts: bool = True,
    show_ledger_entries: bool = True,
) -> str:
    """Render a FlowIR instance as a Mermaid sequence diagram.

    If flow_config is provided and has optional_groups, steps belonging
    to those groups are wrapped in ``opt {label}`` blocks.
    """
    og_step_ids: dict[str, str] = {}
    if flow_config:
        for og in flow_config.optional_groups:
            for s in og.steps:
                og_step_ids[s.step_id] = og.label

    actors = flow_config.actors if flow_config else {}
    participants: dict[str, str] = {}

    for step in flow_ir.steps:
        src, dest = _resolve_step_participants(step, actors)
        src_key = src.replace(" ", "")
        dest_key = dest.replace(" ", "")
        if src_key not in participants:
            participants[src_key] = src
        if dest_key not in participants:
            participants[dest_key] = dest

    lines: list[str] = ["sequenceDiagram", "    autonumber"]

    for key, display in participants.items():
        lines.append(f"    participant {key} as {display}")

    lines.append("")
    all_part_keys = list(participants.keys())
    if len(all_part_keys) >= 2:
        lines.append(
            f"    Note over {all_part_keys[0]},{all_part_keys[-1]}: "
            f"{flow_ir.trace_value}"
        )
    elif all_part_keys:
        lines.append(f"    Note over {all_part_keys[0]}: {flow_ir.trace_value}")

    current_group: str | None = None

    for step in flow_ir.steps:
        step_group = og_step_ids.get(step.step_id)

        if step_group != current_group:
            if current_group is not None:
                lines.append("    end")
            if step_group is not None:
                lines.append(f"    opt {step_group}")
            current_group = step_group

        src, dest = _resolve_step_participants(step, actors)
        src_key = src.replace(" ", "")
        dest_key = dest.replace(" ", "")
        arrow = _ARROW_BY_TYPE.get(step.resource_type, "->>")

        desc = step.payload.get("description", step.step_id)
        desc = desc.replace(";", ",").replace("#", "").replace("%%", "pct")
        if show_amounts:
            amount = step.payload.get("amount")
            if amount is not None:
                desc += f" ${amount / 100:,.2f}"

        lines.append(f"    {src_key}{arrow}{dest_key}: {desc}")

        if show_ledger_entries and step.ledger_groups:
            for lg in step.ledger_groups:
                if not lg.entries:
                    continue
                entry_parts: list[str] = []
                for entry in lg.entries:
                    direction = _DIR_ABBREV.get(entry.get("direction", ""), entry.get("direction", "?").upper()[:2])
                    acct = actor_display_name(entry.get("ledger_account_id", ""))
                    amt = entry.get("amount", 0)
                    if show_amounts:
                        entry_parts.append(f"{direction} {acct} ${amt / 100:,.2f}")
                    else:
                        entry_parts.append(f"{direction} {acct}")

                acct_refs = [e.get("ledger_account_id", "") for e in lg.entries]
                acct_names = [actor_display_name(r) if r else "Ledger" for r in acct_refs]
                acct_keys = list(dict.fromkeys(n.replace(" ", "") for n in acct_names))
                if len(acct_keys) >= 2:
                    note_over = f"{acct_keys[0]},{acct_keys[1]}"
                elif acct_keys:
                    note_over = acct_keys[0]
                else:
                    note_over = dest_key

                note_text = "<br/>".join(entry_parts)
                lines.append(f"    Note over {note_over}: {note_text}")

    if current_group is not None:
        lines.append("    end")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public gate (wired into main.py at step 1s)
# ---------------------------------------------------------------------------


def maybe_compile(
    config: DataLoaderConfig,
) -> tuple[DataLoaderConfig, list[FlowIR] | None]:
    """If funds_flows is populated, compile to FlowIR and emit back into
    the config's resource sections.  Returns (compiled_config, flow_irs).
    flow_irs is None when no funds_flows were present.

    Flows with ``instance_resources`` are expanded for a single default
    instance (0000) so they work without the generation pipeline.
    """
    if not config.funds_flows:
        return config, None

    extra_resources: dict[str, list[dict]] = {}
    expanded_flows: list[FundsFlowConfig] = []

    default_profile = {
        "first_name": "Demo",
        "last_name": "User",
        "business_name": "Demo Corp",
        "industry": "fintech",
        "country": "US",
    }

    for flow in config.funds_flows:
        if flow.instance_resources:
            mapping = {"instance": "0000", "ref": flow.ref, **default_profile}

            expanded_ir = _expand_instance_resources(
                flow.instance_resources, 0, default_profile,
            )
            for section, items in expanded_ir.items():
                extra_resources.setdefault(section, []).extend(items)

            flow_dict = flow.model_dump()
            flow_dict.pop("instance_resources", None)
            flow_dict = deep_format_map(flow_dict, mapping)
            expanded_flows.append(FundsFlowConfig.model_validate(flow_dict))
        else:
            needs_expansion = any(
                "{instance}" in v or "{first_name}" in v
                for v in flow.actors.values()
            )
            if needs_expansion:
                mapping = {"instance": "0000", "ref": flow.ref, **default_profile}
                flow_dict = flow.model_dump()
                flow_dict = deep_format_map(flow_dict, mapping)
                expanded_flows.append(FundsFlowConfig.model_validate(flow_dict))
            else:
                expanded_flows.append(flow)

    flow_irs = compile_flows(expanded_flows, config)
    emitted = emit_dataloader_config(
        flow_irs, base_config=config, extra_resources=extra_resources,
    )
    return emitted, flow_irs


# ---------------------------------------------------------------------------
# Backend utilities for the UI
# ---------------------------------------------------------------------------


def compute_flow_status(flow_ir: FlowIR) -> str:
    """Compute aggregate flow status from FlowIR at compile time.

    Returns "preview" for compile-time data; post-execution should query
    actual LT statuses from RunManifest.
    """
    return "preview"


def flow_account_deltas(flow_ir: FlowIR) -> dict[str, int]:
    """Compute net balance delta per ledger account for one flow instance.

    Positive = net debit, negative = net credit.
    """
    deltas: dict[str, int] = {}
    for step in flow_ir.steps:
        for lg in step.ledger_groups:
            for entry in lg.entries:
                acct = entry.get("ledger_account_id", "")
                amount = entry.get("amount", 0)
                direction = entry.get("direction", "")
                signed = amount if direction == "debit" else -amount
                deltas[acct] = deltas.get(acct, 0) + signed
    return deltas


def compile_diagnostics(flow_irs: list[FlowIR]) -> dict:
    """Compute compile-time diagnostics across all FlowIR instances."""
    type_counts: dict[str, int] = {}
    trace_values: set[str] = set()
    total_steps = 0
    total_entries = 0
    flow_metadata_keys: set[str] = set()

    for ir in flow_irs:
        trace_values.add(ir.trace_value)
        for step in ir.steps:
            total_steps += 1
            type_counts[step.resource_type] = type_counts.get(step.resource_type, 0) + 1
            for lg in step.ledger_groups:
                total_entries += len(lg.entries)
            for k in step.trace_metadata:
                if k.startswith("_flow_"):
                    flow_metadata_keys.add(k)

    return {
        "type_counts": type_counts,
        "total_steps": total_steps,
        "total_entries": total_entries,
        "trace_values": sorted(trace_values)[:20],
        "trace_value_count": len(trace_values),
        "flow_metadata_keys": sorted(flow_metadata_keys),
    }
