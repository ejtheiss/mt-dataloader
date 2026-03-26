"""Compilation pipeline: types, pass functions, and orchestrator.

Defines ``AuthoringConfig``, ``CompilationContext``, ``ExecutionPlan``,
the five standard passes, ``STANDARD_PIPELINE``, and ``compile_to_plan``.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass
from typing import Callable

from models import DataLoaderConfig, FundsFlowConfig, GenerationRecipeV1

from .core import compile_flows, emit_dataloader_config
from .generation import _expand_instance_resources, deep_format_map
from .ir import StepRelationships, build_step_relationships
from .mermaid import render_mermaid

# ---------------------------------------------------------------------------
# Pipeline types — immutable boundaries between compiler stages
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthoringConfig:
    """Immutable user-authored config — the 'source program.'

    ``from_json()`` deep-copies the parsed ``DataLoaderConfig`` so this
    object holds the sole reference.  Pipeline passes must not hold
    references to ``authoring.config`` internals — always deep-copy
    before mutating.
    """

    config: DataLoaderConfig
    json_text: str
    source_hash: str

    @staticmethod
    def from_json(raw: bytes) -> AuthoringConfig:
        parsed = DataLoaderConfig.model_validate_json(raw)
        return AuthoringConfig(
            config=parsed.model_copy(deep=True),
            json_text=raw.decode(),
            source_hash=hashlib.sha256(raw).hexdigest(),
        )


@dataclass(frozen=True)
class CompilationContext:
    """Immutable context threaded through pipeline passes.

    Each pass receives a context and returns a new context via
    ``dataclasses.replace()`` with additional fields populated.
    """

    authoring: AuthoringConfig
    recipe: GenerationRecipeV1 | None = None

    seed_profiles: tuple[dict, ...] = ()
    expanded_flows: tuple[FundsFlowConfig, ...] = ()
    extra_resources: tuple[tuple[str, list[dict]], ...] = ()
    relationships: tuple[StepRelationships, ...] = ()

    flow_irs: tuple = ()

    flat_config: DataLoaderConfig | None = None

    batches: tuple[tuple[str, ...], ...] = ()
    preview_items: tuple[dict, ...] = ()

    mermaid_diagrams: tuple[str, ...] = ()

    view_data: tuple = ()


@dataclass(frozen=True)
class ExecutionPlan:
    """Frozen output of the compilation pipeline — the 'plan file.'"""

    config: DataLoaderConfig
    flow_irs: tuple
    expanded_flows: tuple[FundsFlowConfig, ...]
    mermaid_diagrams: tuple[str, ...]
    batches: tuple[tuple[str, ...], ...]
    preview_items: tuple[dict, ...]
    source_hash: str
    view_data: tuple = ()


# ---------------------------------------------------------------------------
# Pass Pipeline
# ---------------------------------------------------------------------------

Pass = Callable[[CompilationContext], CompilationContext]


def pass_expand_instances(ctx: CompilationContext) -> CompilationContext:
    """Pass 1: Expand instance_resources, build per-flow StepRelationships."""
    config = ctx.authoring.config
    expanded_flows: list[FundsFlowConfig] = []
    extra_resources: list[tuple[str, list[dict]]] = []

    default_profile = {
        "first_name": "Demo", "last_name": "User",
        "business_name": "Demo Corp", "industry": "fintech", "country": "US",
    }

    for flow in config.funds_flows:
        mapping = {"instance": "0000", "ref": flow.ref, **default_profile}
        if flow.instance_resources:
            expanded_ir = _expand_instance_resources(flow.instance_resources, 0, default_profile)
            for section, items in expanded_ir.items():
                extra_resources.append((section, items))
        flow_dict = flow.model_dump()
        flow_dict.pop("instance_resources", None)
        saved_trace_tpl = flow_dict.get("trace_value_template")
        flow_dict = deep_format_map(flow_dict, mapping)
        if saved_trace_tpl is not None:
            flow_dict["trace_value_template"] = saved_trace_tpl
        expanded_flows.append(FundsFlowConfig.model_validate(flow_dict))

    rels = tuple(
        build_step_relationships(f.steps, f.optional_groups)
        for f in expanded_flows
    )

    return dataclasses.replace(
        ctx,
        expanded_flows=tuple(expanded_flows),
        extra_resources=tuple(extra_resources),
        relationships=rels,
    )


def pass_compile_to_ir(ctx: CompilationContext) -> CompilationContext:
    """Pass 3: Compile expanded flows into FlowIR instances."""
    base = ctx.flat_config or ctx.authoring.config
    irs = compile_flows(list(ctx.expanded_flows), base)
    return dataclasses.replace(ctx, flow_irs=tuple(irs))


def pass_emit_resources(ctx: CompilationContext) -> CompilationContext:
    """Pass 4: Emit FlowIR steps back into config resource sections."""
    base_dict = ctx.authoring.config.model_dump(exclude_none=True)

    for section, items in ctx.extra_resources:
        existing = base_dict.setdefault(section, [])
        seen_refs = {
            item.get("ref") for item in existing
            if isinstance(item, dict) and item.get("ref")
        }
        for item in items:
            ref = item.get("ref") if isinstance(item, dict) else None
            if ref and ref in seen_refs:
                continue
            existing.append(item)
            if ref:
                seen_refs.add(ref)

    base_dict["funds_flows"] = []

    base_config = DataLoaderConfig.model_validate(base_dict)
    emitted = emit_dataloader_config(list(ctx.flow_irs), base_config)

    return dataclasses.replace(ctx, flat_config=emitted)


def pass_render_diagrams(ctx: CompilationContext) -> CompilationContext:
    """Pass 7: Render Mermaid diagrams for each flow."""
    diagrams: list[str] = []
    flows = ctx.expanded_flows
    cname = ctx.authoring.config.customer_name
    for i, ir in enumerate(ctx.flow_irs):
        fc = flows[i] if i < len(flows) else None
        diagrams.append(render_mermaid(ir, flow_config=fc, customer_name=cname))
    return dataclasses.replace(ctx, mermaid_diagrams=tuple(diagrams))


def pass_compute_view_data(ctx: CompilationContext) -> CompilationContext:
    """Pass 8: Compute per-view row/column data from FlowIR + config."""
    from flow_views import compute_view_data as _compute

    flows = ctx.expanded_flows
    views = []
    for i, ir in enumerate(ctx.flow_irs):
        fc = flows[i] if i < len(flows) else None
        if fc:
            views.append(_compute(ir, fc))
        else:
            from flow_views import FlowViewData
            views.append(FlowViewData())
    return dataclasses.replace(ctx, view_data=tuple(views))


STANDARD_PIPELINE: tuple[Pass, ...] = (
    pass_expand_instances,
    pass_compile_to_ir,
    pass_emit_resources,
    pass_render_diagrams,
    pass_compute_view_data,
)


def compile_to_plan(
    authoring: AuthoringConfig,
    *,
    recipe: GenerationRecipeV1 | None = None,
    pipeline: tuple[Pass, ...] = STANDARD_PIPELINE,
) -> ExecutionPlan:
    """Run the full compilation pipeline and return a frozen ExecutionPlan."""
    ctx = CompilationContext(authoring=authoring, recipe=recipe)
    for p in pipeline:
        ctx = p(ctx)
    return ExecutionPlan(
        config=ctx.flat_config or ctx.authoring.config,
        flow_irs=ctx.flow_irs,
        expanded_flows=ctx.expanded_flows,
        mermaid_diagrams=ctx.mermaid_diagrams,
        batches=ctx.batches,
        preview_items=ctx.preview_items,
        source_hash=ctx.authoring.source_hash,
        view_data=ctx.view_data,
    )
