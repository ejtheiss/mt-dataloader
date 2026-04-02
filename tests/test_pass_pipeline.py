"""Tests for the formal pass pipeline (Plan 1 Phase 2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from flow_compiler import (
    AuthoringConfig,
    CompilationContext,
    ExecutionPlan,
    compile_to_plan,
    pass_compile_to_ir,
    pass_compute_view_data,
    pass_emit_resources,
    pass_expand_instances,
    pass_render_diagrams,
)
from tests.paths import EXAMPLES_DIR


def _authoring_from_file(path: Path) -> AuthoringConfig:
    raw = path.read_bytes()
    return AuthoringConfig.from_json(raw)


class TestPassExpandInstances:
    def test_simple_flow_expands(self):
        auth = _authoring_from_file(EXAMPLES_DIR / "funds_flow_demo.json")
        ctx = CompilationContext(authoring=auth)
        out = pass_expand_instances(ctx)
        assert len(out.expanded_flows) >= 1
        assert len(out.relationships) == len(out.expanded_flows)

    def test_instance_resources_expanded(self):
        auth = _authoring_from_file(EXAMPLES_DIR / "stablecoin_ramp.json")
        ctx = CompilationContext(authoring=auth)
        out = pass_expand_instances(ctx)
        assert len(out.extra_resources) > 0


class TestPassCompileToIr:
    def test_produces_flow_irs(self):
        auth = _authoring_from_file(EXAMPLES_DIR / "funds_flow_demo.json")
        ctx = CompilationContext(authoring=auth)
        ctx = pass_expand_instances(ctx)
        ctx = pass_compile_to_ir(ctx)
        assert len(ctx.flow_irs) >= 1

    def test_irs_are_frozen(self):
        auth = _authoring_from_file(EXAMPLES_DIR / "funds_flow_demo.json")
        ctx = CompilationContext(authoring=auth)
        ctx = pass_expand_instances(ctx)
        ctx = pass_compile_to_ir(ctx)
        import dataclasses

        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.flow_irs[0].flow_ref = "mutated"  # type: ignore[misc]


class TestPassEmitResources:
    def test_flat_config_populated(self):
        auth = _authoring_from_file(EXAMPLES_DIR / "funds_flow_demo.json")
        ctx = CompilationContext(authoring=auth)
        ctx = pass_expand_instances(ctx)
        ctx = pass_compile_to_ir(ctx)
        ctx = pass_emit_resources(ctx)
        assert ctx.flat_config is not None
        assert len(ctx.flat_config.funds_flows) == 0


class TestPassRenderDiagrams:
    def test_mermaid_diagrams_generated(self):
        auth = _authoring_from_file(EXAMPLES_DIR / "funds_flow_demo.json")
        ctx = CompilationContext(authoring=auth)
        ctx = pass_expand_instances(ctx)
        ctx = pass_compile_to_ir(ctx)
        ctx = pass_render_diagrams(ctx)
        assert len(ctx.mermaid_diagrams) == len(ctx.flow_irs)
        for d in ctx.mermaid_diagrams:
            assert d.startswith("sequenceDiagram")


class TestPassComputeViewData:
    def test_view_data_populated(self):
        auth = _authoring_from_file(EXAMPLES_DIR / "stablecoin_ramp.json")
        ctx = CompilationContext(authoring=auth)
        ctx = pass_expand_instances(ctx)
        ctx = pass_compile_to_ir(ctx)
        ctx = pass_compute_view_data(ctx)
        assert len(ctx.view_data) == len(ctx.flow_irs)
        for vd in ctx.view_data:
            assert hasattr(vd, "available_views")


class TestCompileToPlan:
    def test_returns_execution_plan(self):
        auth = _authoring_from_file(EXAMPLES_DIR / "funds_flow_demo.json")
        plan = compile_to_plan(auth)
        assert isinstance(plan, ExecutionPlan)
        assert plan.config is not None
        assert len(plan.flow_irs) >= 1
        assert len(plan.mermaid_diagrams) >= 1

    def test_plan_is_frozen(self):
        auth = _authoring_from_file(EXAMPLES_DIR / "funds_flow_demo.json")
        plan = compile_to_plan(auth)
        import dataclasses

        with pytest.raises(dataclasses.FrozenInstanceError):
            plan.source_hash = "changed"  # type: ignore[misc]

    def test_idempotent(self):
        auth = _authoring_from_file(EXAMPLES_DIR / "funds_flow_demo.json")
        plan1 = compile_to_plan(auth)
        plan2 = compile_to_plan(auth)
        assert plan1.source_hash == plan2.source_hash
        assert len(plan1.flow_irs) == len(plan2.flow_irs)

    @pytest.mark.parametrize(
        "json_file",
        sorted(EXAMPLES_DIR.glob("*.json")),
        ids=lambda p: p.stem,
    )
    def test_all_examples_compile_via_pipeline(self, json_file):
        auth = _authoring_from_file(json_file)
        plan = compile_to_plan(auth)
        assert isinstance(plan, ExecutionPlan)
        assert len(plan.mermaid_diagrams) == len(plan.flow_irs)


class TestCustomPipeline:
    def test_partial_pipeline(self):
        auth = _authoring_from_file(EXAMPLES_DIR / "funds_flow_demo.json")
        plan = compile_to_plan(
            auth,
            pipeline=(
                pass_expand_instances,
                pass_compile_to_ir,
            ),
        )
        assert len(plan.flow_irs) >= 1
        assert len(plan.mermaid_diagrams) == 0
