"""Tests for Phase 1C pipeline types: AuthoringConfig, CompilationContext, ExecutionPlan."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path

import pytest

from flow_compiler import (
    AuthoringConfig,
    CompilationContext,
    ExecutionPlan,
    FlowIR,
    compile_to_plan,
)
from models import DataLoaderConfig
from tests.paths import EXAMPLES_DIR


def _minimal_json() -> bytes:
    return json.dumps({}).encode()


def _demo_json() -> bytes:
    return (EXAMPLES_DIR / "funds_flow_demo.json").read_bytes()


# ---------------------------------------------------------------------------
# AuthoringConfig
# ---------------------------------------------------------------------------


class TestAuthoringConfig:
    def test_from_json_parses(self):
        raw = _minimal_json()
        ac = AuthoringConfig.from_json(raw)
        assert isinstance(ac.config, DataLoaderConfig)

    def test_from_json_preserves_text(self):
        raw = _minimal_json()
        ac = AuthoringConfig.from_json(raw)
        assert ac.json_text == raw.decode()

    def test_from_json_computes_hash(self):
        raw = _minimal_json()
        ac = AuthoringConfig.from_json(raw)
        assert ac.source_hash == hashlib.sha256(raw).hexdigest()

    def test_frozen(self):
        ac = AuthoringConfig.from_json(_minimal_json())
        with pytest.raises(dataclasses.FrozenInstanceError):
            ac.config = DataLoaderConfig()

    def test_deep_copy_isolation(self):
        raw = _demo_json()
        ac = AuthoringConfig.from_json(raw)
        original_count = len(ac.config.funds_flows)
        parsed_separately = DataLoaderConfig.model_validate_json(raw)
        parsed_separately.funds_flows.clear()
        assert len(ac.config.funds_flows) == original_count

    def test_from_json_with_funds_flows(self):
        raw = _demo_json()
        ac = AuthoringConfig.from_json(raw)
        assert len(ac.config.funds_flows) > 0

    def test_from_json_invalid_json(self):
        with pytest.raises(Exception):
            AuthoringConfig.from_json(b"not valid json{{{")


# ---------------------------------------------------------------------------
# CompilationContext
# ---------------------------------------------------------------------------


class TestCompilationContext:
    def test_default_fields(self):
        ac = AuthoringConfig.from_json(_minimal_json())
        ctx = CompilationContext(authoring=ac)
        assert ctx.recipe is None
        assert ctx.seed_profiles == ()
        assert ctx.expanded_flows == ()
        assert ctx.extra_resources == ()
        assert ctx.relationships == ()
        assert ctx.flow_irs == ()
        assert ctx.flat_config is None
        assert ctx.batches == ()
        assert ctx.preview_items == ()
        assert ctx.mermaid_diagrams == ()
        assert ctx.view_data == ()

    def test_frozen(self):
        ac = AuthoringConfig.from_json(_minimal_json())
        ctx = CompilationContext(authoring=ac)
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.flow_irs = ()

    def test_replace_returns_new(self):
        ac = AuthoringConfig.from_json(_minimal_json())
        ctx = CompilationContext(authoring=ac)
        ir = FlowIR(
            flow_ref="f",
            instance_id="0000",
            pattern_type="t",
            trace_key="k",
            trace_value="v",
            trace_metadata={},
        )
        new_ctx = dataclasses.replace(ctx, flow_irs=(ir,))
        assert new_ctx.flow_irs == (ir,)
        assert ctx.flow_irs == ()

    def test_replace_preserves_authoring(self):
        ac = AuthoringConfig.from_json(_minimal_json())
        ctx = CompilationContext(authoring=ac)
        new_ctx = dataclasses.replace(ctx, mermaid_diagrams=("graph TD;",))
        assert new_ctx.authoring is ctx.authoring


# ---------------------------------------------------------------------------
# ExecutionPlan
# ---------------------------------------------------------------------------


class TestExecutionPlan:
    def test_construction(self):
        plan = ExecutionPlan(
            config=DataLoaderConfig(),
            flow_irs=(),
            expanded_flows=(),
            mermaid_diagrams=(),
            batches=(),
            preview_items=(),
            source_hash="abc123",
        )
        assert plan.source_hash == "abc123"
        assert plan.view_data == ()
        assert plan.expanded_flows == ()

    def test_frozen(self):
        plan = ExecutionPlan(
            config=DataLoaderConfig(),
            flow_irs=(),
            expanded_flows=(),
            mermaid_diagrams=(),
            batches=(),
            preview_items=(),
            source_hash="abc123",
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            plan.config = DataLoaderConfig()

    def test_source_hash_links_to_authoring(self):
        ac = AuthoringConfig.from_json(_minimal_json())
        plan = ExecutionPlan(
            config=ac.config,
            flow_irs=(),
            expanded_flows=(),
            mermaid_diagrams=(),
            batches=(),
            preview_items=(),
            source_hash=ac.source_hash,
        )
        assert plan.source_hash == ac.source_hash


# ---------------------------------------------------------------------------
# Integration smoke
# ---------------------------------------------------------------------------


class TestIntegrationSmoke:
    @pytest.mark.parametrize(
        "json_file",
        sorted(EXAMPLES_DIR.glob("*.json")),
        ids=lambda p: p.stem,
    )
    def test_authoring_from_example_json(self, json_file: Path):
        raw = json_file.read_bytes()
        ac = AuthoringConfig.from_json(raw)
        assert isinstance(ac.config, DataLoaderConfig)
        assert ac.source_hash == hashlib.sha256(raw).hexdigest()

    def test_roundtrip_authoring_to_plan(self):
        raw = _demo_json()
        ac = AuthoringConfig.from_json(raw)
        plan = compile_to_plan(ac)
        assert plan.source_hash == ac.source_hash
        assert len(plan.flow_irs) > 0
        assert plan.config.funds_flows == []
        assert len(plan.expanded_flows) > 0
