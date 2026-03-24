"""Funds Flow DSL compiler package.

Pipeline: FundsFlowConfig[] → resolve_actors → compile_flows → FlowIR[]
          → emit_dataloader_config → DataLoaderConfig

Re-exports the full public API so existing ``from flow_compiler import …``
statements continue to work without modification.
"""

# --- IR types and relationship index ---
from .ir import (
    FlowIR,
    FlowIRStep,
    LedgerGroup,
    StepRelationships,
    build_step_relationships,
    _ref_account_type,
)

# --- Core compiler and emitter ---
from .core import (
    _auto_derive_lifecycle_refs,
    _find_reverse_target,
    _flip_entry,
    _validate_ref_segment,
    _with_lifecycle_depends_on,
    compile_flows,
    emit_dataloader_config,
    expand_trace_value,
    flatten_actor_refs,
    flatten_optional_groups,
    resolve_actors,
)

# --- Pipeline types, passes, and orchestrator ---
from .pipeline import (
    AuthoringConfig,
    CompilationContext,
    ExecutionPlan,
    Pass,
    STANDARD_PIPELINE,
    compile_to_plan,
    pass_expand_instances,
    pass_compile_to_ir,
    pass_emit_resources,
    pass_render_diagrams,
    pass_compute_view_data,
)

# --- Generation pipeline ---
from .generation import (
    GenerationResult,
    deep_format_map,
    _expand_instance_resources,
    clone_flow,
    apply_overrides,
    apply_amount_variance,
    preselect_edge_cases,
    activate_optional_groups,
    mark_staged,
    select_staged_instances,
    generate_from_recipe,
)

# --- Mermaid rendering ---
from .mermaid import (
    MermaidSequenceBuilder,
    _build_ref_display_map,
    _classify_participant,
    _collect_participants,
    _find_parent_step,
    _resolve_step_participants,
    render_mermaid,
    actor_display_name,
    _resolve_actor_display,
    _resolve_ipd_source,
)

# --- Diagnostics ---
from .diagnostics import (
    compute_flow_status,
    flow_account_deltas,
    compile_diagnostics,
)

# --- Timing & seasoning ---
from .timing import (
    compute_effective_dates,
    compute_spread_offsets,
)

__all__ = [
    "flatten_optional_groups",
    "render_mermaid",
    "clone_flow",
    "apply_overrides",
    "apply_amount_variance",
    "preselect_edge_cases",
    "activate_optional_groups",
    "mark_staged",
    "select_staged_instances",
    "GenerationResult",
    "generate_from_recipe",
    "actor_display_name",
    "compute_flow_status",
    "flow_account_deltas",
    "compile_diagnostics",
    "deep_format_map",
    "StepRelationships",
    "build_step_relationships",
    "AuthoringConfig",
    "CompilationContext",
    "ExecutionPlan",
    "MermaidSequenceBuilder",
    "_ref_account_type",
    "_build_ref_display_map",
    "_resolve_actor_display",
    "FlowIR",
    "FlowIRStep",
    "LedgerGroup",
    "Pass",
    "STANDARD_PIPELINE",
    "compile_to_plan",
    "pass_expand_instances",
    "pass_compile_to_ir",
    "pass_emit_resources",
    "pass_render_diagrams",
    "pass_compute_view_data",
    "compile_flows",
    "emit_dataloader_config",
    "flatten_actor_refs",
    "flatten_optional_groups",
    "resolve_actors",
    "_expand_instance_resources",
    "_resolve_ipd_source",
    "clone_flow",
    "compute_effective_dates",
    "compute_spread_offsets",
]
