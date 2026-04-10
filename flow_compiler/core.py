"""Core DSL compiler: FundsFlowConfig → FlowIR → DataLoaderConfig.

Contains ``compile_flows``, actor resolution, lifecycle derivation
(``core_lifecycle``), optional-group flattening (``core_optional_groups``),
and per-step compile helpers. Emission lives in ``core_emit``.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from models import (
    ActorFrame,
    ActorSlot,
    DataLoaderConfig,
    FundsFlowConfig,
    PaymentOrderStep,
    _StepBase,
)

from .core_emit import (
    _validate_account_roles,
)
from .core_emit import (
    emit_dataloader_config as emit_dataloader_config,
)
from .core_lifecycle import (
    _auto_derive_lifecycle_refs,
    _find_reverse_target,
    _flip_entry,
)
from .core_lifecycle import (
    _with_lifecycle_depends_on as _with_lifecycle_depends_on,
)
from .core_optional_groups import flatten_optional_groups as flatten_optional_groups
from .ir import FlowIR, FlowIRStep, LedgerGroup

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _internal_account_ref_key(ref: str) -> str | None:
    """Return ``internal_account.<ref>`` key if *ref* targets an internal account."""
    prefix = "$ref:internal_account."
    if not ref.startswith(prefix):
        return None
    tail = ref[len(prefix) :]
    if not tail:
        return None
    # First path segment only (``foo.bar`` not used for IAs today).
    base = tail.split(".", 1)[0]
    return f"internal_account.{base}"


def _ledger_account_ref_for_internal(internal_account_ref: str) -> str:
    """``$ref:internal_account.X`` → ``$ref:internal_account.X.ledger_account`` (MT child)."""
    if not internal_account_ref.startswith("$ref:internal_account."):
        raise ValueError(f"Expected internal account ref, got {internal_account_ref!r}")
    body = internal_account_ref.removeprefix("$ref:")
    return f"$ref:{body}.ledger_account"


def _internal_account_currency(base: DataLoaderConfig, ia_key: str) -> str | None:
    """Resolve ``internal_account.<ref>`` to configured currency."""
    suffix = ia_key.removeprefix("internal_account.")
    for ia in base.internal_accounts:
        if ia.ref == suffix:
            return ia.currency
    return None


def _cross_currency_internal_book(
    base: DataLoaderConfig,
    originating_account_id: str,
    receiving_account_id: str,
) -> bool:
    """True when both legs are internal accounts with different currencies (e.g. USD↔USDC)."""
    o_key = _internal_account_ref_key(originating_account_id)
    r_key = _internal_account_ref_key(receiving_account_id)
    if not o_key or not r_key:
        return False
    co = _internal_account_currency(base, o_key)
    cr = _internal_account_currency(base, r_key)
    if not co or not cr:
        return False
    return co != cr


def _validate_ref_segment(segment: str) -> None:
    if "__" in segment:
        raise ValueError(
            f"Ref segment '{segment}' must not contain '__' (reserved as instance separator)"
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
            raise ValueError(f"Unknown actor ref '{key}' — available: {sorted(actor_refs.keys())}")
        return actor_refs[key]
    if isinstance(obj, dict):
        return {k: resolve_actors(v, actor_refs) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_actors(v, actor_refs) for v in obj]
    return obj


def expand_trace_value(
    template: str,
    ref: str,
    instance: int,
    profile: dict[str, str] | None = None,
) -> str:
    from collections import defaultdict

    mapping: dict[str, Any] = {"ref": ref, "instance": instance}
    if profile:
        mapping.update(profile)
    try:
        return template.format_map(defaultdict(str, mapping))
    except (ValueError, KeyError) as e:
        raise ValueError(f"Bad placeholder in trace metadata template '{template}': {e}") from e


# ---------------------------------------------------------------------------
# Optional group flattening — see core_optional_groups
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Compiler: DSL → FlowIR
# ---------------------------------------------------------------------------


_DSL_ONLY_FIELDS = frozenset(
    {
        "step_id",
        "type",
        "depends_on",
        "ledger_entries",
        "ledger_status",
        "ledger_inline",
        "staged",
        "fulfills",
    }
)


def _compile_step(
    step: _StepBase,
    flow: FundsFlowConfig,
    instance_id: str,
    trace_meta: dict[str, str],
    step_ref_map: dict[str, str],
    all_steps: list[_StepBase],
    actor_refs: dict[str, str],
    og_step_ids: dict[str, str],
    base_config: DataLoaderConfig,
) -> FlowIRStep:
    """Compile a single DSL step into a FlowIRStep."""
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
                ledger_groups.append(
                    LedgerGroup(
                        group_id=f"{step.step_id}_lg0",
                        inline=inline,
                        entries=tuple(reversed_entries),
                        metadata=trace_meta.copy(),
                        status=status,
                    )
                )
    elif isinstance(entries, list) and entries:
        entries_resolved = resolve_actors(
            [e.model_dump(exclude_none=True) for e in entries],
            actor_refs,
        )
        inline = getattr(step, "ledger_inline", False)
        status = getattr(step, "ledger_status", None)
        ledger_groups.append(
            LedgerGroup(
                group_id=f"{step.step_id}_lg0",
                inline=inline,
                entries=tuple(entries_resolved),
                metadata=trace_meta.copy(),
                status=status,
            )
        )
    elif (
        isinstance(step, PaymentOrderStep)
        and step_dict.get("type") == "book"
        and entries is None
        and isinstance(step_dict.get("originating_account_id"), str)
        and isinstance(step_dict.get("receiving_account_id"), str)
        and _cross_currency_internal_book(
            base_config,
            step_dict["originating_account_id"],
            step_dict["receiving_account_id"],
        )
    ):
        # MT auto-LT for cross-currency book uses mixed unit semantics; emit an explicit
        # balanced inline LT with the same minor-unit amount on both legs (USD-style cents
        # for USD and USDC — Tradeify / stablecoin ramp).
        amt = int(step_dict.get("amount") or 0)
        if amt > 0:
            orig_ref = step_dict["originating_account_id"]
            recv_ref = step_dict["receiving_account_id"]
            la_orig = _ledger_account_ref_for_internal(orig_ref)
            la_recv = _ledger_account_ref_for_internal(recv_ref)
            direction = step_dict.get("direction", "credit")
            if direction == "credit":
                debit_la, credit_la = la_recv, la_orig
            else:
                debit_la, credit_la = la_orig, la_recv
            auto_entries = (
                {"amount": amt, "direction": "debit", "ledger_account_id": debit_la},
                {"amount": amt, "direction": "credit", "ledger_account_id": credit_la},
            )
            ledger_groups.append(
                LedgerGroup(
                    group_id=f"{step.step_id}_lg_auto_xcur_book",
                    inline=True,
                    entries=auto_entries,
                    metadata=trace_meta.copy(),
                    status=getattr(step, "ledger_status", None),
                )
            )

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
        try:
            primary_tpl = flow.trace_metadata[flow.trace_key]
        except KeyError as e:
            raise ValueError(
                f"Flow {flow.ref!r}: trace_metadata must include key {flow.trace_key!r} "
                f"(primary trace template)"
            ) from e
        trace_value = expand_trace_value(primary_tpl, flow.ref, int(instance_id))
        extras = {k: v for k, v in flow.trace_metadata.items() if k != flow.trace_key}
        trace_meta = {flow.trace_key: trace_value, **extras}

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
            ir_steps.append(
                _compile_step(
                    step,
                    flow,
                    instance_id,
                    trace_meta,
                    step_ref_map,
                    all_steps,
                    actor_refs,
                    og_step_ids,
                    base_config,
                )
            )

        # --- Pass 3: compile optional group steps (preview_only for Mermaid) ---
        main_step_ids = [s.step_id for s in ir_steps]
        for og in flow.optional_groups:
            compiled_og: list[FlowIRStep] = []
            for step in og.steps:
                ir_step = _compile_step(
                    step,
                    flow,
                    instance_id,
                    trace_meta,
                    step_ref_map,
                    all_steps,
                    actor_refs,
                    og_step_ids,
                    base_config,
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

        result.append(
            FlowIR(
                flow_ref=flow.ref,
                instance_id=instance_id,
                pattern_type=flow.pattern_type,
                trace_key=flow.trace_key,
                trace_value=trace_value,
                trace_metadata=trace_meta,
                steps=tuple(ir_steps),
            )
        )

    return result
