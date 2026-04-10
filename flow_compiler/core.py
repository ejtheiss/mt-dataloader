"""Core DSL compiler: FundsFlowConfig → FlowIR → DataLoaderConfig.

Contains ``compile_flows`` orchestration and optional-group flattening.
Actor resolution and trace expansion live in ``core_actors`` (Plan 08 A4-5);
per-step compilation in ``core_step_compile``; emission in ``core_emit``.
"""

from __future__ import annotations

import dataclasses

from models import DataLoaderConfig, FundsFlowConfig, _StepBase

from .core_actors import (
    expand_trace_value as expand_trace_value,
)
from .core_actors import (
    flatten_actor_refs as flatten_actor_refs,
)
from .core_actors import (
    resolve_actors as resolve_actors,
)
from .core_emit import (
    emit_dataloader_config as emit_dataloader_config,
)
from .core_lifecycle import (
    _auto_derive_lifecycle_refs as _auto_derive_lifecycle_refs,
)
from .core_lifecycle import (
    _find_reverse_target as _find_reverse_target,
)
from .core_lifecycle import (
    _flip_entry as _flip_entry,
)
from .core_lifecycle import (
    _with_lifecycle_depends_on as _with_lifecycle_depends_on,
)
from .core_optional_groups import flatten_optional_groups as flatten_optional_groups
from .ir import FlowIR, FlowIRStep

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _validate_ref_segment(segment: str) -> None:
    if "__" in segment:
        raise ValueError(
            f"Ref segment '{segment}' must not contain '__' (reserved as instance separator)"
        )


# ---------------------------------------------------------------------------
# Optional group flattening — see core_optional_groups
# ---------------------------------------------------------------------------

# Imported after ``resolve_actors`` is defined so ``core_step_compile`` can lazy-import core.
from .core_step_compile import _compile_step  # noqa: E402

# ---------------------------------------------------------------------------
# Compiler: DSL → FlowIR (orchestration)
# ---------------------------------------------------------------------------


def _flow_instance_id_and_trace_meta(
    flow: FundsFlowConfig,
) -> tuple[str, str, dict[str, str]]:
    """Parse ``flow.ref`` for instance id; build ``trace_value`` and merged ``trace_meta``."""
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
    return instance_id, trace_value, trace_meta


def _optional_group_step_ids(flow: FundsFlowConfig) -> dict[str, str]:
    """Map optional-group step_id → group label (schema + stamped ``_flow_optional_group``)."""
    og_step_ids: dict[str, str] = {}
    for og in flow.optional_groups:
        for s in og.steps:
            og_step_ids[s.step_id] = og.label
    for s in flow.steps:
        meta = s.metadata
        if "_flow_optional_group" in meta and s.step_id not in og_step_ids:
            og_step_ids[s.step_id] = meta["_flow_optional_group"]
    return og_step_ids


def _all_steps_main_and_optional(flow: FundsFlowConfig) -> list[_StepBase]:
    """Flatten main ``steps`` plus every optional group's steps (compile ref map over all)."""
    all_steps: list[_StepBase] = list(flow.steps)
    for og in flow.optional_groups:
        all_steps.extend(og.steps)
    return all_steps


def _typed_step_ref_map(
    flow: FundsFlowConfig,
    instance_id: str,
    all_steps: list[_StepBase],
) -> dict[str, str]:
    """step_id → ``$ref:<type>.<flow_ref>__<instance>__<step_id>`` for main + OG steps."""
    step_ref_map: dict[str, str] = {}
    for step in all_steps:
        _validate_ref_segment(step.step_id)
        emitted_ref = f"{flow.ref}__{instance_id}__{step.step_id}"
        typed_ref = f"$ref:{step.type}.{emitted_ref}"
        step_ref_map[step.step_id] = typed_ref
    return step_ref_map


def _compile_main_steps_to_ir(
    flow: FundsFlowConfig,
    instance_id: str,
    trace_meta: dict[str, str],
    step_ref_map: dict[str, str],
    all_steps: list[_StepBase],
    actor_refs: dict[str, str],
    og_step_ids: dict[str, str],
    base_config: DataLoaderConfig,
) -> list[FlowIRStep]:
    """Compile only main-line ``flow.steps`` into ``FlowIRStep`` list."""
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
    return ir_steps


def _splice_optional_group_preview_steps(
    flow: FundsFlowConfig,
    instance_id: str,
    trace_meta: dict[str, str],
    step_ref_map: dict[str, str],
    all_steps: list[_StepBase],
    actor_refs: dict[str, str],
    og_step_ids: dict[str, str],
    base_config: DataLoaderConfig,
    ir_steps: list[FlowIRStep],
) -> None:
    """Compile optional-group steps as ``preview_only`` and insert per OG position rules.

    Mutates ``ir_steps`` in place.
    """
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

        if not anchor:
            latest_idx = -1
            for og_step in og.steps:
                for dep in og_step.depends_on:
                    if dep in main_step_ids:
                        idx = main_step_ids.index(dep)
                        if idx > latest_idx:
                            latest_idx = idx
                            anchor = dep
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


def _compile_one_flow(flow: FundsFlowConfig, base_config: DataLoaderConfig) -> FlowIR:
    """Compile a single ``FundsFlowConfig`` (main steps + optional groups) to ``FlowIR``."""
    instance_id, trace_value, trace_meta = _flow_instance_id_and_trace_meta(flow)
    og_step_ids = _optional_group_step_ids(flow)
    all_steps = _all_steps_main_and_optional(flow)
    step_ref_map = _typed_step_ref_map(flow, instance_id, all_steps)
    actor_refs = flatten_actor_refs(flow.actors)

    ir_steps = _compile_main_steps_to_ir(
        flow,
        instance_id,
        trace_meta,
        step_ref_map,
        all_steps,
        actor_refs,
        og_step_ids,
        base_config,
    )
    _splice_optional_group_preview_steps(
        flow,
        instance_id,
        trace_meta,
        step_ref_map,
        all_steps,
        actor_refs,
        og_step_ids,
        base_config,
        ir_steps,
    )

    return FlowIR(
        flow_ref=flow.ref,
        instance_id=instance_id,
        pattern_type=flow.pattern_type,
        trace_key=flow.trace_key,
        trace_value=trace_value,
        trace_metadata=trace_meta,
        steps=tuple(ir_steps),
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
    return [_compile_one_flow(flow, base_config) for flow in flows]
