"""Core DSL compiler: FundsFlowConfig → FlowIR → DataLoaderConfig.

Contains ``compile_flows``, ``emit_dataloader_config``, actor resolution,
lifecycle derivation helpers, and optional-group flattening.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from models import (
    RESOURCE_TYPE_TO_SECTION,
    ActorFrame,
    ActorSlot,
    DataLoaderConfig,
    ExpectedPaymentStep,
    FundsFlowConfig,
    IncomingPaymentDetailStep,
    LedgerTransactionStep,
    PaymentOrderStep,
    ReturnStep,
    ReversalStep,
    TransitionLedgerTransactionStep,
    _StepBase,
)

from .ir import FlowIR, FlowIRStep, LedgerGroup

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_ref_segment(segment: str) -> None:
    if "__" in segment:
        raise ValueError(
            f"Ref segment '{segment}' must not contain '__' "
            f"(reserved as instance separator)"
        )


def flatten_actor_refs(actors: dict[str, ActorFrame]) -> dict[str, str]:
    """Build a flat ``frame.slot → $ref:`` mapping from actor frames.

    Used by ``resolve_actors`` and Mermaid rendering to translate
    ``@actor:frame.slot`` references into concrete ``$ref:`` strings.
    """
    flat: dict[str, str] = {}
    for frame_name, frame in actors.items():
        for slot_name, slot in frame.slots.items():
            ref = slot.ref if isinstance(slot, ActorSlot) else slot
            flat[f"{frame_name}.{slot_name}"] = ref
    return flat


def resolve_actors(obj: Any, actor_refs: dict[str, str]) -> Any:
    """Replace ``@actor:frame.slot`` references with concrete ``$ref:`` values.

    ``actor_refs`` is a pre-flattened map from ``flatten_actor_refs``.
    """
    if isinstance(obj, str) and obj.startswith("@actor:"):
        key = obj[7:]
        if key not in actor_refs:
            raise ValueError(
                f"Unknown actor ref '{key}' — "
                f"available: {sorted(actor_refs.keys())}"
            )
        return actor_refs[key]
    if isinstance(obj, dict):
        return {k: resolve_actors(v, actor_refs) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_actors(v, actor_refs) for v in obj]
    return obj


def expand_trace_value(template: str, ref: str, instance: int) -> str:
    try:
        return template.format_map({"ref": ref, "instance": instance})
    except KeyError as e:
        raise ValueError(
            f"Unknown placeholder {e} in trace_value_template '{template}'"
        ) from e


def _auto_derive_lifecycle_refs(
    step: _StepBase,
    step_dict: dict,
    step_ref_map: dict[str, str],
    all_steps: list[_StepBase],
) -> None:
    """Auto-set returnable_id / payment_order_id / ledger_transaction_id from depends_on targets."""
    if isinstance(step, ReturnStep) and "returnable_id" not in step_dict:
        for dep_id in step.depends_on:
            dep_step = next((s for s in all_steps if s.step_id == dep_id), None)
            if dep_step is None:
                continue
            if isinstance(dep_step, IncomingPaymentDetailStep):
                step_dict["returnable_id"] = step_ref_map[dep_id]
                break

    if isinstance(step, ReversalStep) and "payment_order_id" not in step_dict:
        for dep_id in step.depends_on:
            dep_step = next((s for s in all_steps if s.step_id == dep_id), None)
            if dep_step is None:
                continue
            if isinstance(dep_step, PaymentOrderStep):
                step_dict["payment_order_id"] = step_ref_map[dep_id]
                break

    if isinstance(step, TransitionLedgerTransactionStep) and "ledger_transaction_id" not in step_dict:
        for dep_id in step.depends_on:
            dep_step = next((s for s in all_steps if s.step_id == dep_id), None)
            if dep_step is None:
                continue
            if isinstance(dep_step, LedgerTransactionStep):
                step_dict["ledger_transaction_id"] = step_ref_map[dep_id]
                break
            if isinstance(dep_step, (PaymentOrderStep, ExpectedPaymentStep, ReturnStep, ReversalStep)):
                entries = getattr(dep_step, "ledger_entries", None)
                inline = getattr(dep_step, "ledger_inline", False)
                if isinstance(entries, list) and entries and inline:
                    parent_ref = step_ref_map[dep_id]
                    step_dict["ledger_transaction_id"] = f"{parent_ref}.ledger_transaction"
                    break


def _find_reverse_target(
    step: _StepBase,
    all_steps: list[_StepBase],
) -> _StepBase | None:
    """Find the parent step whose ledger entries should be reversed."""
    target_id: str | None = None
    if isinstance(step, ReturnStep) and step.returnable_id:
        target_id = step.returnable_id
    elif isinstance(step, ReversalStep) and step.payment_order_id:
        target_id = step.payment_order_id

    if target_id:
        match = next((s for s in all_steps if s.step_id == target_id), None)
        if match:
            return match

    for dep_id in step.depends_on:
        dep = next((s for s in all_steps if s.step_id == dep_id), None)
        if dep:
            dep_entries = getattr(dep, "ledger_entries", None)
            if isinstance(dep_entries, list) and dep_entries:
                return dep
    return None


def _flip_entry(entry_dict: dict) -> dict:
    """Return a copy of a ledger entry dict with direction flipped."""
    flipped = dict(entry_dict)
    if flipped.get("direction") == "debit":
        flipped["direction"] = "credit"
    elif flipped.get("direction") == "credit":
        flipped["direction"] = "debit"
    return flipped


def _with_lifecycle_depends_on(step: FlowIRStep) -> FlowIRStep:
    """Return step with lifecycle depends_on edges added.

    Pure function — returns a new FlowIRStep via dataclasses.replace()
    if edges need adding, otherwise returns the original object.
    """
    extra: list[str] = []

    if step.resource_type == "return":
        returnable_ref = step.payload.get("returnable_id", "")
        if returnable_ref.startswith("$ref:") and returnable_ref not in step.depends_on:
            extra.append(returnable_ref)
    elif step.resource_type == "reversal":
        po_ref = step.payload.get("payment_order_id", "")
        if po_ref.startswith("$ref:") and po_ref not in step.depends_on:
            extra.append(po_ref)
    elif step.resource_type == "transition_ledger_transaction":
        lt_ref = step.payload.get("ledger_transaction_id", "")
        if lt_ref.startswith("$ref:") and lt_ref not in step.depends_on:
            extra.append(lt_ref)

    if not extra:
        return step

    return dataclasses.replace(
        step,
        depends_on=step.depends_on + tuple(extra),
    )


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

    Respects ``position`` and ``insert_after``:
    - ``"after"`` + no anchor -> append to end (default)
    - ``"after"`` + ``insert_after: "X"`` -> insert after step X
    - ``"before"`` + no anchor -> prepend to start
    - ``"before"`` + ``insert_after: "X"`` -> insert before step X
    - ``"replace"`` + ``insert_after: "X"`` -> remove step X, insert
      group steps in its place, rewrite downstream depends_on

    Mutates and returns flow_dict. Removes the optional_groups key.
    """
    optional_groups = flow_dict.pop("optional_groups", [])
    steps: list[dict] = flow_dict.setdefault("steps", [])

    for og in optional_groups:
        if activated_groups is not None and og["label"] not in activated_groups:
            continue

        position = og.get("position", "after")
        anchor = og.get("insert_after")
        og_steps = og["steps"]

        if position == "replace" and anchor:
            anchor_idx = next(
                (i for i, s in enumerate(steps) if s.get("step_id") == anchor),
                None,
            )
            if anchor_idx is not None:
                steps.pop(anchor_idx)
                for j, new_step in enumerate(og_steps):
                    steps.insert(anchor_idx + j, new_step)
                last_new_id = og_steps[-1].get("step_id")
                if last_new_id:
                    for s in steps:
                        deps = s.get("depends_on")
                        if deps and anchor in deps:
                            s["depends_on"] = [
                                last_new_id if d == anchor else d for d in deps
                            ]
            else:
                steps.extend(og_steps)

        elif position == "before":
            if anchor:
                anchor_idx = next(
                    (i for i, s in enumerate(steps) if s.get("step_id") == anchor),
                    None,
                )
                if anchor_idx is not None:
                    for j, new_step in enumerate(og_steps):
                        steps.insert(anchor_idx + j, new_step)
                else:
                    steps.extend(og_steps)
            else:
                for j, new_step in enumerate(og_steps):
                    steps.insert(j, new_step)

        else:
            if anchor:
                anchor_idx = next(
                    (i for i, s in enumerate(steps) if s.get("step_id") == anchor),
                    None,
                )
                if anchor_idx is not None:
                    for j, new_step in enumerate(og_steps):
                        steps.insert(anchor_idx + 1 + j, new_step)
                else:
                    steps.extend(og_steps)
            else:
                steps.extend(og_steps)

    return flow_dict


_EXTERNAL_ACCOUNT_PREFIXES = frozenset({"counterparty", "external_account"})
_INTERNAL_ACCOUNT_PREFIXES = frozenset({"internal_account"})


def _validate_account_roles(
    step: _StepBase, resolved: dict[str, Any], flow_ref: str,
) -> None:
    """Validate that account refs are appropriate for the step type."""
    orig = resolved.get("originating_account_id", "")
    if not orig:
        return

    prefix = orig.replace("$ref:", "").split(".")[0]

    if step.type == "payment_order":
        if prefix in _EXTERNAL_ACCOUNT_PREFIXES:
            raise ValueError(
                f"Flow '{flow_ref}', step '{step.step_id}': "
                f"originating_account_id must be an internal account "
                f"(got '{orig}' which is a {prefix}). "
                f"The ODFI for a payment order is always a platform IA."
            )
    elif step.type in ("incoming_payment_detail", "expected_payment"):
        if prefix in _INTERNAL_ACCOUNT_PREFIXES:
            raise ValueError(
                f"Flow '{flow_ref}', step '{step.step_id}': "
                f"originating_account_id on an IPD/EP must be an "
                f"external account — the ODFI is the sender's EA "
                f"(got '{orig}' which is an internal_account)."
            )


# ---------------------------------------------------------------------------
# Compiler: DSL → FlowIR
# ---------------------------------------------------------------------------


_DSL_ONLY_FIELDS = frozenset({
    "step_id", "type", "depends_on",
    "ledger_entries", "ledger_status", "ledger_inline",
    "staged", "fulfills",
})


def _compile_step(
    step: _StepBase,
    flow: FundsFlowConfig,
    instance_id: str,
    trace_meta: dict[str, str],
    step_ref_map: dict[str, str],
    all_steps: list[_StepBase],
    actor_refs: dict[str, str],
    og_step_ids: dict[str, str],
) -> FlowIRStep:
    """Compile a single DSL step into a FlowIRStep."""
    emitted_ref = f"{flow.ref}__{instance_id}__{step.step_id}"

    exclude = _DSL_ONLY_FIELDS & set(type(step).model_fields)
    step_dict = step.model_dump(exclude=exclude, exclude_none=True)

    if "payment_type" in step_dict:
        step_dict["type"] = step_dict.pop("payment_type")

    step_dict = resolve_actors(step_dict, actor_refs)
    _validate_account_roles(step, step_dict, flow.ref)

    step_dict["metadata"] = {
        **step_dict.get("metadata", {}),
        **trace_meta,
    }

    if getattr(step, "staged", False):
        step_dict["staged"] = True

    ir_depends = [step_ref_map[dep] for dep in step.depends_on if dep in step_ref_map]

    _auto_derive_lifecycle_refs(step, step_dict, step_ref_map, all_steps)

    ledger_groups: list[LedgerGroup] = []
    entries = getattr(step, "ledger_entries", None)
    if entries == "reverse_parent":
        parent = _find_reverse_target(step, all_steps)
        if parent:
            parent_entries = getattr(parent, "ledger_entries", None)
            if isinstance(parent_entries, list) and parent_entries:
                reversed_entries = resolve_actors(
                    [_flip_entry(e.model_dump(exclude_none=True)) for e in parent_entries],
                    actor_refs,
                )
                inline = getattr(step, "ledger_inline", False)
                status = getattr(step, "ledger_status", None)
                ledger_groups.append(LedgerGroup(
                    group_id=f"{step.step_id}_lg0",
                    inline=inline,
                    entries=tuple(reversed_entries),
                    metadata=trace_meta.copy(),
                    status=status,
                ))
    elif isinstance(entries, list) and entries:
        entries_resolved = resolve_actors(
            [e.model_dump(exclude_none=True) for e in entries],
            actor_refs,
        )
        inline = getattr(step, "ledger_inline", False)
        status = getattr(step, "ledger_status", None)
        ledger_groups.append(LedgerGroup(
            group_id=f"{step.step_id}_lg0",
            inline=inline,
            entries=tuple(entries_resolved),
            metadata=trace_meta.copy(),
            status=status,
        ))

    return FlowIRStep(
        step_id=step.step_id,
        flow_ref=flow.ref,
        instance_id=instance_id,
        depends_on=tuple(ir_depends),
        resource_type=step.type,
        payload=step_dict,
        ledger_groups=tuple(ledger_groups),
        trace_metadata=trace_meta,
        optional_group=og_step_ids.get(step.step_id),
    )


def compile_flows(
    flows: list[FundsFlowConfig],
    base_config: DataLoaderConfig,
) -> list[FlowIR]:
    """Compile FundsFlowConfig DSL entries into FlowIR instances.

    Compiles both main ``steps`` and ``optional_groups`` steps into
    FlowIR. Optional group steps carry ``optional_group`` metadata so
    Mermaid can render ``opt``/``alt`` blocks.
    """
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
        for s in flow.steps:
            meta = s.metadata
            if "_flow_optional_group" in meta and s.step_id not in og_step_ids:
                og_step_ids[s.step_id] = meta["_flow_optional_group"]

        all_steps: list[_StepBase] = list(flow.steps)
        for og in flow.optional_groups:
            all_steps.extend(og.steps)

        # --- Pass 1: build ref map for ALL steps (main + OG) ---
        step_ref_map: dict[str, str] = {}
        for step in all_steps:
            _validate_ref_segment(step.step_id)
            emitted_ref = f"{flow.ref}__{instance_id}__{step.step_id}"
            typed_ref = f"$ref:{step.type}.{emitted_ref}"
            step_ref_map[step.step_id] = typed_ref

        actor_refs = flatten_actor_refs(flow.actors)

        # --- Pass 2: compile main steps ---
        ir_steps: list[FlowIRStep] = []
        for step in flow.steps:
            ir_steps.append(_compile_step(
                step, flow, instance_id, trace_meta,
                step_ref_map, all_steps, actor_refs, og_step_ids,
            ))

        # --- Pass 3: compile optional group steps (preview_only for Mermaid) ---
        main_step_ids = [s.step_id for s in ir_steps]
        for og in flow.optional_groups:
            compiled_og: list[FlowIRStep] = []
            for step in og.steps:
                ir_step = _compile_step(
                    step, flow, instance_id, trace_meta,
                    step_ref_map, all_steps, actor_refs, og_step_ids,
                )
                compiled_og.append(dataclasses.replace(ir_step, preview_only=True))

            position = getattr(og, "position", "after")
            anchor = getattr(og, "insert_after", None)

            # Auto-infer anchor from OG steps' depends_on when not explicit
            if not anchor:
                latest_idx = -1
                for og_step in og.steps:
                    for dep in og_step.depends_on:
                        if dep in main_step_ids:
                            idx = main_step_ids.index(dep)
                            if idx > latest_idx:
                                latest_idx = idx
                                anchor = dep
                # "before" without anchor means prepend to start
                if not anchor and position != "before":
                    ir_steps.extend(compiled_og)
                    main_step_ids = [s.step_id for s in ir_steps]
                    continue

            if position == "before" and not anchor:
                for j, s in enumerate(compiled_og):
                    ir_steps.insert(j, s)
            elif anchor and anchor in main_step_ids:
                idx = main_step_ids.index(anchor)
                if position == "before":
                    for j, s in enumerate(compiled_og):
                        ir_steps.insert(idx + j, s)
                else:
                    for j, s in enumerate(compiled_og):
                        ir_steps.insert(idx + 1 + j, s)
            else:
                ir_steps.extend(compiled_og)
            main_step_ids = [s.step_id for s in ir_steps]

        result.append(FlowIR(
            flow_ref=flow.ref,
            instance_id=instance_id,
            pattern_type=flow.pattern_type,
            trace_key=flow.trace_key,
            trace_value=trace_value,
            trace_metadata=trace_meta,
            steps=tuple(ir_steps),
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
    """Emit FlowIR steps into DataLoaderConfig resource sections."""
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
            if step.preview_only:
                continue
            step = _with_lifecycle_depends_on(step)

            ref = step.emitted_ref
            resource_type = step.resource_type
            section = RESOURCE_TYPE_TO_SECTION[resource_type]

            resource_dict: dict[str, Any] = {
                "ref": ref,
                **step.payload,
            }
            if resource_type in ("incoming_payment_detail", "expected_payment"):
                resource_dict.pop("originating_account_id", None)
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
                        parent_typed_ref = f"$ref:{resource_type}.{ref}"
                        lt_dict: dict[str, Any] = {
                            "ref": lt_ref,
                            "ledger_entries": lg.entries,
                            "metadata": lg.metadata,
                            "depends_on": [parent_typed_ref],
                            "ledgerable_type": resource_type,
                            "ledgerable_id": parent_typed_ref,
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
