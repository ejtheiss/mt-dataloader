"""Funds Flow DSL compiler package.

Pipeline: FundsFlowConfig[] → resolve_actors → compile_flows → FlowIR[]
          → emit_dataloader_config → DataLoaderConfig

Re-exports the full public API so existing ``from flow_compiler import …``
statements continue to work without modification.
"""

# --- IR types and relationship index ---
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

# --- Diagnostics ---
from .diagnostics import (
    compile_diagnostics,
    compute_flow_status,
    flow_account_deltas,
)

# --- Public display helpers for flow views (non-underscore API) ---
from .display import (
    build_ref_display_map,
    ref_account_type,
    resolve_actor_display,
)

# --- Generation pipeline ---
from .generation import (
    GenerationResult,
    _expand_instance_resources,
    activate_optional_groups,
    apply_amount_variance,
    apply_overrides,
    clone_flow,
    deep_format_map,
    generate_from_recipe,
    mark_staged,
    preselect_edge_cases,
    select_staged_instances,
)
from .ir import (
    FlowIR,
    FlowIRStep,
    LedgerGroup,
    StepRelationships,
    _ref_account_type,
    build_step_relationships,
)

# --- Mermaid rendering ---
from .mermaid import (
    MermaidSequenceBuilder,
    _build_ref_display_map,
    _classify_participant,
    _collect_participants,
    _find_parent_step,
    _resolve_actor_display,
    _resolve_ipd_source,
    _resolve_step_participants,
    actor_display_name,
    render_mermaid,
)

# --- Pipeline types, passes, and orchestrator ---
from .pipeline import (
    STANDARD_PIPELINE,
    AuthoringConfig,
    CompilationContext,
    ExecutionPlan,
    Pass,
    compile_to_plan,
    pass_compile_to_ir,
    pass_compute_view_data,
    pass_emit_resources,
    pass_expand_instances,
    pass_render_diagrams,
)

# --- Timing & seasoning ---
from .timing import (
    compute_effective_dates,
    compute_spread_offsets,
)

__all__ = [
    "_auto_derive_lifecycle_refs",
    "_classify_participant",
    "_collect_participants",
    "_expand_instance_resources",
    "_find_parent_step",
    "_find_reverse_target",
    "_flip_entry",
    "_ref_account_type",
    "_resolve_ipd_source",
    "_resolve_actor_display",
    "_resolve_step_participants",
    "_validate_ref_segment",
    "_with_lifecycle_depends_on",
    "expand_trace_value",
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
    "_build_ref_display_map",
    "build_ref_display_map",
    "ref_account_type",
    "resolve_actor_display",
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
    "resolve_actors",
    "compute_effective_dates",
    "compute_spread_offsets",
]
