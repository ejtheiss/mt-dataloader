"""Tests for Step 4 — Fund Flow UI, Session Hardening, Remaining Phases.

Covers: maybe_compile() tuple signature, compute_flow_status,
flow_account_deltas, compile_diagnostics, actor_display_name rename,
FlowIRStep.optional_group, RunManifest new fields, backward-compat load,
and passthrough regression.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import RunManifest, _now_iso
from flow_compiler import (
    FlowIR,
    FlowIRStep,
    LedgerGroup,
    actor_display_name,
    compile_diagnostics,
    compile_flows,
    compute_flow_status,
    flow_account_deltas,
    maybe_compile,
    render_mermaid,
)
from models import DataLoaderConfig, FundsFlowConfig

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_funds_flow_config() -> DataLoaderConfig:
    demo = EXAMPLES_DIR / "funds_flow_demo.json"
    return DataLoaderConfig.model_validate_json(demo.read_text())


def _make_flow_ir(
    entries: list[dict] | None = None,
    optional_group: str | None = None,
) -> FlowIR:
    """Build a minimal FlowIR for testing."""
    lg = LedgerGroup(
        group_id="lg0",
        inline=False,
        entries=entries or [
            {"ledger_account_id": "$ref:ledger_account.cash", "direction": "debit", "amount": 10000},
            {"ledger_account_id": "$ref:ledger_account.revenue", "direction": "credit", "amount": 10000},
        ],
        metadata={},
        status=None,
    )
    step = FlowIRStep(
        step_id="step1",
        flow_ref="test",
        instance_id="0000",
        depends_on=[],
        resource_type="ledger_transaction",
        payload={"description": "Test LT"},
        ledger_groups=[lg],
        trace_metadata={"_flow_test": "yes"},
        optional_group=optional_group,
    )
    return FlowIR(
        flow_ref="test",
        instance_id="0000",
        pattern_type="test_pattern",
        trace_key="test_key",
        trace_value="test-0000",
        trace_metadata={"_flow_test": "yes"},
        steps=[step],
    )


# ---------------------------------------------------------------------------
# maybe_compile() tuple signature
# ---------------------------------------------------------------------------


class TestMaybeCompileTuple:
    def test_no_funds_flows_returns_none(self):
        config = DataLoaderConfig()
        result, flow_irs = maybe_compile(config)
        assert flow_irs is None
        assert result.funds_flows == []

    def test_with_funds_flows_returns_tuple(self):
        config = _load_funds_flow_config()
        assert len(config.funds_flows) > 0
        result, flow_irs = maybe_compile(config)
        assert flow_irs is not None
        assert len(flow_irs) > 0
        assert result.funds_flows == []

    def test_flow_irs_have_correct_length(self):
        config = _load_funds_flow_config()
        _, flow_irs = maybe_compile(config)
        assert len(flow_irs) == len(_load_funds_flow_config().funds_flows)


# ---------------------------------------------------------------------------
# compute_flow_status()
# ---------------------------------------------------------------------------


class TestComputeFlowStatus:
    def test_compile_time_returns_preview(self):
        ir = _make_flow_ir()
        assert compute_flow_status(ir) == "preview"


# ---------------------------------------------------------------------------
# flow_account_deltas()
# ---------------------------------------------------------------------------


class TestFlowAccountDeltas:
    def test_basic_debit_credit(self):
        ir = _make_flow_ir([
            {"ledger_account_id": "acct_a", "direction": "debit", "amount": 5000},
            {"ledger_account_id": "acct_b", "direction": "credit", "amount": 5000},
        ])
        deltas = flow_account_deltas(ir)
        assert deltas["acct_a"] == 5000
        assert deltas["acct_b"] == -5000

    def test_empty_entries(self):
        ir = _make_flow_ir()
        ir.steps[0].ledger_groups = []
        assert flow_account_deltas(ir) == {}

    def test_multiple_entries_accumulate(self):
        ir = _make_flow_ir([
            {"ledger_account_id": "acct_a", "direction": "debit", "amount": 3000},
            {"ledger_account_id": "acct_a", "direction": "credit", "amount": 1000},
        ])
        deltas = flow_account_deltas(ir)
        assert deltas["acct_a"] == 2000


# ---------------------------------------------------------------------------
# compile_diagnostics()
# ---------------------------------------------------------------------------


class TestCompileDiagnostics:
    def test_basic_diagnostics(self):
        ir = _make_flow_ir()
        diag = compile_diagnostics([ir])
        assert diag["total_steps"] == 1
        assert diag["type_counts"]["ledger_transaction"] == 1
        assert diag["total_entries"] == 2
        assert diag["trace_value_count"] == 1
        assert "test-0000" in diag["trace_values"]
        assert "_flow_test" in diag["flow_metadata_keys"]

    def test_multiple_flows(self):
        ir1 = _make_flow_ir()
        ir2 = _make_flow_ir()
        ir2.trace_value = "test-0001"
        diag = compile_diagnostics([ir1, ir2])
        assert diag["total_steps"] == 2
        assert diag["trace_value_count"] == 2


# ---------------------------------------------------------------------------
# actor_display_name() (renamed from _actor_display_name)
# ---------------------------------------------------------------------------


class TestActorDisplayName:
    def test_ref_format(self):
        assert actor_display_name("$ref:internal_account.ops_usd") == "Ops Usd"

    def test_simple_name(self):
        assert actor_display_name("some_account") == "Some Account"


# ---------------------------------------------------------------------------
# FlowIRStep.optional_group
# ---------------------------------------------------------------------------


class TestFlowIRStepOptionalGroup:
    def test_default_is_none(self):
        step = FlowIRStep(
            step_id="s1", flow_ref="f", instance_id="0000",
            depends_on=[], resource_type="payment_order",
            payload={}, ledger_groups=[], trace_metadata={},
        )
        assert step.optional_group is None

    def test_set_optional_group(self):
        step = FlowIRStep(
            step_id="s1", flow_ref="f", instance_id="0000",
            depends_on=[], resource_type="payment_order",
            payload={}, ledger_groups=[], trace_metadata={},
            optional_group="ach_return",
        )
        assert step.optional_group == "ach_return"

    def test_compile_populates_optional_group(self):
        """When optional group steps are flattened with metadata stamps,
        compile_flows tags them with the group label."""
        config = _load_funds_flow_config()
        has_ogs = any(fc.optional_groups for fc in config.funds_flows)
        if not has_ogs:
            pytest.skip("Demo has no optional_groups")
        # Simulate generation pipeline: stamp metadata then flatten
        flows_with_og: list[FundsFlowConfig] = []
        for fc in config.funds_flows:
            d = fc.model_dump()
            for og in d.get("optional_groups", []):
                for step in og.get("steps", []):
                    step.setdefault("metadata", {})
                    step["metadata"]["_flow_optional_group"] = og["label"]
            from flow_compiler import flatten_optional_groups
            flatten_optional_groups(d, activated_groups=None)
            flows_with_og.append(FundsFlowConfig.model_validate(d))

        flow_irs = compile_flows(flows_with_og, config)
        has_tagged = False
        for ir in flow_irs:
            for step in ir.steps:
                if step.optional_group:
                    has_tagged = True
        assert has_tagged, "Expected at least one step with optional_group set after flatten"


# ---------------------------------------------------------------------------
# RunManifest new fields
# ---------------------------------------------------------------------------


class TestRunManifestNewFields:
    def test_defaults_to_none(self):
        m = RunManifest(run_id="test", config_hash="sha256:abc")
        assert m.generation_recipe is None
        assert m.compile_id is None
        assert m.seed_version is None

    def test_to_dict_includes_new_fields(self):
        m = RunManifest(run_id="test", config_hash="sha256:abc")
        m.generation_recipe = {"flow_ref": "deposit", "instances": 100}
        m.compile_id = "c123"
        m.seed_version = "v1"
        d = m._to_dict()
        assert d["generation_recipe"] == {"flow_ref": "deposit", "instances": 100}
        assert d["compile_id"] == "c123"
        assert d["seed_version"] == "v1"

    def test_load_with_new_fields(self):
        m = RunManifest(run_id="test", config_hash="sha256:abc")
        m.generation_recipe = {"flow_ref": "x"}
        m.compile_id = "c1"
        m.seed_version = "sv1"
        m.finalize("completed")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(m._to_dict(), f)
            f.flush()
            loaded = RunManifest.load(f.name)

        assert loaded.generation_recipe == {"flow_ref": "x"}
        assert loaded.compile_id == "c1"
        assert loaded.seed_version == "sv1"

    def test_load_backward_compat_legacy_manifest(self):
        """Legacy manifests without new fields should load without error."""
        legacy = {
            "run_id": "legacy",
            "config_hash": "sha256:old",
            "started_at": _now_iso(),
            "status": "completed",
            "resources_created": [],
            "resources_failed": [],
            "resources_staged": [],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(legacy, f)
            f.flush()
            loaded = RunManifest.load(f.name)

        assert loaded.generation_recipe is None
        assert loaded.compile_id is None
        assert loaded.seed_version is None


# ---------------------------------------------------------------------------
# Passthrough regression
# ---------------------------------------------------------------------------


class TestPassthroughRegression:
    def test_existing_demo_compiles(self):
        config = _load_funds_flow_config()
        result, flow_irs = maybe_compile(config)
        assert flow_irs is not None
        assert len(result.funds_flows) == 0

    def test_empty_config_compiles(self):
        config = DataLoaderConfig()
        result, flow_irs = maybe_compile(config)
        assert flow_irs is None

    def test_render_mermaid_still_works(self):
        config = _load_funds_flow_config()
        flow_irs = compile_flows(config.funds_flows, config)
        for ir, fc in zip(flow_irs[:2], config.funds_flows[:2]):
            text = render_mermaid(ir, fc)
            assert "sequenceDiagram" in text
