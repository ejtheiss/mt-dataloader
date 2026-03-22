"""Tests for step 1s: schema models, compiler gate, seed catalog."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from models import (
    DataLoaderConfig,
    FundsFlowConfig,
    FundsFlowScaleConfig,
    FundsFlowStepConfig,
    IncomingPaymentDetailConfig,
    ReturnConfig,
)
from flow_compiler import maybe_compile


# ---------------------------------------------------------------------------
# FundsFlowStepConfig
# ---------------------------------------------------------------------------


class TestFundsFlowStepConfig:
    def test_valid_step(self):
        step = FundsFlowStepConfig(
            step_id="deposit",
            type="incoming_payment_detail",
            direction="credit",
            amount=50000,
        )
        assert step.step_id == "deposit"
        assert step.type == "incoming_payment_detail"

    def test_invalid_step_type(self):
        with pytest.raises(ValueError, match="not supported"):
            FundsFlowStepConfig(step_id="bad", type="not_a_real_type")

    def test_extra_fields_allowed(self):
        step = FundsFlowStepConfig(
            step_id="deposit",
            type="incoming_payment_detail",
            internal_account_id="$ref:internal_account.ops",
        )
        assert step.model_extra["internal_account_id"] == "$ref:internal_account.ops"

    def test_unbalanced_ledger_entries(self):
        with pytest.raises(ValueError, match="unbalanced"):
            FundsFlowStepConfig(
                step_id="bad_lt",
                type="ledger_transaction",
                ledger_entries=[
                    {"amount": 100, "direction": "debit",
                     "ledger_account_id": "$ref:ledger_account.cash"},
                    {"amount": 200, "direction": "credit",
                     "ledger_account_id": "$ref:ledger_account.revenue"},
                ],
            )

    def test_balanced_ledger_entries(self):
        step = FundsFlowStepConfig(
            step_id="settle",
            type="ledger_transaction",
            ledger_entries=[
                {"amount": 50000, "direction": "debit",
                 "ledger_account_id": "$ref:ledger_account.cash"},
                {"amount": 50000, "direction": "credit",
                 "ledger_account_id": "$ref:ledger_account.revenue"},
            ],
        )
        assert len(step.ledger_entries) == 2

    @pytest.mark.parametrize("step_type", [
        "payment_order", "incoming_payment_detail", "ledger_transaction",
        "expected_payment", "return", "reversal",
    ])
    def test_all_valid_step_types(self, step_type: str):
        step = FundsFlowStepConfig(step_id="s1", type=step_type)
        assert step.type == step_type


# ---------------------------------------------------------------------------
# FundsFlowConfig
# ---------------------------------------------------------------------------


class TestFundsFlowConfig:
    def test_valid_flow(self):
        flow = FundsFlowConfig(
            ref="test_flow",
            pattern_type="deposit_settle",
            steps=[
                FundsFlowStepConfig(step_id="s1", type="incoming_payment_detail"),
            ],
        )
        assert flow.ref == "test_flow"
        assert len(flow.steps) == 1

    def test_duplicate_step_ids(self):
        with pytest.raises(ValueError, match="Duplicate step_id"):
            FundsFlowConfig(
                ref="bad_flow",
                pattern_type="test",
                steps=[
                    FundsFlowStepConfig(step_id="s1", type="incoming_payment_detail"),
                    FundsFlowStepConfig(step_id="s1", type="ledger_transaction"),
                ],
            )

    def test_invalid_depends_on(self):
        with pytest.raises(ValueError, match="not a valid step_id"):
            FundsFlowConfig(
                ref="bad_flow",
                pattern_type="test",
                steps=[
                    FundsFlowStepConfig(
                        step_id="s1",
                        type="ledger_transaction",
                        depends_on=["nonexistent"],
                    ),
                ],
            )

    def test_bad_trace_placeholder(self):
        with pytest.raises(ValueError, match="unknown placeholders"):
            FundsFlowConfig(
                ref="bad",
                pattern_type="test",
                trace_value_template="{ref}-{bad_key}",
                steps=[
                    FundsFlowStepConfig(step_id="s1", type="incoming_payment_detail"),
                ],
            )

    def test_empty_steps_rejected(self):
        with pytest.raises(ValueError):
            FundsFlowConfig(ref="empty", pattern_type="test", steps=[])

    def test_valid_depends_on(self):
        flow = FundsFlowConfig(
            ref="chained",
            pattern_type="test",
            steps=[
                FundsFlowStepConfig(step_id="s1", type="incoming_payment_detail"),
                FundsFlowStepConfig(
                    step_id="s2", type="ledger_transaction", depends_on=["s1"]
                ),
            ],
        )
        assert flow.steps[1].depends_on == ["s1"]

    def test_default_trace_template(self):
        flow = FundsFlowConfig(
            ref="f1",
            pattern_type="test",
            steps=[FundsFlowStepConfig(step_id="s1", type="incoming_payment_detail")],
        )
        assert flow.trace_value_template == "{ref}-{instance}"
        assert flow.trace_key == "deal_id"

    def test_actors_and_metadata(self):
        flow = FundsFlowConfig(
            ref="f1",
            pattern_type="test",
            actors={"acct": "$ref:internal_account.ops"},
            trace_metadata={"env": "demo"},
            steps=[FundsFlowStepConfig(step_id="s1", type="incoming_payment_detail")],
        )
        assert flow.actors == {"acct": "$ref:internal_account.ops"}
        assert flow.trace_metadata == {"env": "demo"}

    def test_scale_config(self):
        flow = FundsFlowConfig(
            ref="f1",
            pattern_type="test",
            scale=FundsFlowScaleConfig(instances=100, mutation_profile="light_variance"),
            steps=[FundsFlowStepConfig(step_id="s1", type="incoming_payment_detail")],
        )
        assert flow.scale.instances == 100
        assert flow.scale.mutation_profile == "light_variance"

    def test_extra_fields_forbidden_on_flow(self):
        with pytest.raises(ValueError):
            FundsFlowConfig(
                ref="f1",
                pattern_type="test",
                bogus_field="nope",
                steps=[FundsFlowStepConfig(step_id="s1", type="incoming_payment_detail")],
            )


# ---------------------------------------------------------------------------
# DataLoaderConfig with funds_flows
# ---------------------------------------------------------------------------


class TestDataLoaderConfigWithFlows:
    def test_existing_config_no_flows(self):
        config = DataLoaderConfig(
            ledgers=[{"ref": "main", "name": "Main"}],
        )
        assert config.funds_flows == []

    def test_config_with_funds_flows(self):
        config = DataLoaderConfig(
            funds_flows=[
                {
                    "ref": "f1",
                    "pattern_type": "deposit",
                    "steps": [{"step_id": "s1", "type": "incoming_payment_detail"}],
                }
            ],
        )
        assert len(config.funds_flows) == 1
        assert config.funds_flows[0].ref == "f1"

    def test_mixed_resources_and_flows(self):
        config = DataLoaderConfig(
            ledgers=[{"ref": "main", "name": "Main"}],
            funds_flows=[
                {
                    "ref": "f1",
                    "pattern_type": "deposit",
                    "steps": [{"step_id": "s1", "type": "incoming_payment_detail"}],
                }
            ],
        )
        assert len(config.ledgers) == 1
        assert len(config.funds_flows) == 1


# ---------------------------------------------------------------------------
# MetadataMixin on IPD and Return
# ---------------------------------------------------------------------------


class TestMetadataOnIPDAndReturn:
    def test_ipd_accepts_metadata(self):
        ipd = IncomingPaymentDetailConfig(
            ref="ipd1",
            type="ach",
            direction="credit",
            amount=10000,
            internal_account_id="$ref:internal_account.ops",
            metadata={"deal_id": "deal-001"},
        )
        assert ipd.metadata == {"deal_id": "deal-001"}

    def test_ipd_metadata_defaults_empty(self):
        ipd = IncomingPaymentDetailConfig(
            ref="ipd1",
            type="ach",
            direction="credit",
            amount=10000,
            internal_account_id="$ref:internal_account.ops",
        )
        assert ipd.metadata == {}

    def test_return_accepts_metadata(self):
        ret = ReturnConfig(
            ref="r1",
            returnable_id="$ref:incoming_payment_detail.ipd1",
            metadata={"deal_id": "deal-001"},
        )
        assert ret.metadata == {"deal_id": "deal-001"}


# ---------------------------------------------------------------------------
# maybe_compile() gate
# ---------------------------------------------------------------------------


class TestMaybeCompile:
    def test_passthrough_no_flows(self):
        config = DataLoaderConfig()
        result = maybe_compile(config)
        assert result is config

    def test_passthrough_with_resources_no_flows(self):
        config = DataLoaderConfig(
            ledgers=[{"ref": "main", "name": "Main"}],
        )
        result = maybe_compile(config)
        assert result is config
        assert len(result.ledgers) == 1

    def test_raises_not_implemented_with_flows(self):
        config = DataLoaderConfig(
            funds_flows=[
                {
                    "ref": "f1",
                    "pattern_type": "deposit",
                    "steps": [{"step_id": "s1", "type": "incoming_payment_detail"}],
                }
            ],
        )
        with pytest.raises(NotImplementedError):
            maybe_compile(config)


# ---------------------------------------------------------------------------
# Existing examples still validate (regression)
# ---------------------------------------------------------------------------

_EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "examples"


class TestExistingExamplesUnchanged:
    @pytest.mark.parametrize(
        "example",
        sorted(_EXAMPLE_DIR.glob("*.json")),
        ids=lambda p: p.name,
    )
    def test_example_validates(self, example: Path):
        raw = example.read_bytes()
        config = DataLoaderConfig.model_validate_json(raw)
        if not config.funds_flows:
            result = maybe_compile(config)
            assert result is config


# ---------------------------------------------------------------------------
# funds_flow_demo.json parses at model level
# ---------------------------------------------------------------------------


class TestFundsFlowDemo:
    def test_demo_parses(self):
        demo = _EXAMPLE_DIR / "funds_flow_demo.json"
        if not demo.exists():
            pytest.skip("funds_flow_demo.json not yet created")
        config = DataLoaderConfig.model_validate_json(demo.read_bytes())
        assert len(config.funds_flows) == 1
        assert config.funds_flows[0].ref == "simple_deposit"
        assert len(config.funds_flows[0].steps) == 2

    def test_demo_hits_not_implemented(self):
        demo = _EXAMPLE_DIR / "funds_flow_demo.json"
        if not demo.exists():
            pytest.skip("funds_flow_demo.json not yet created")
        config = DataLoaderConfig.model_validate_json(demo.read_bytes())
        with pytest.raises(NotImplementedError):
            maybe_compile(config)


# ---------------------------------------------------------------------------
# Seed catalog
# ---------------------------------------------------------------------------

_SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds"


class TestSeedCatalog:
    def test_loads_and_has_sections(self):
        catalog = yaml.safe_load(
            (_SEEDS_DIR / "seed_catalog.yaml").read_text()
        )
        assert "patterns" in catalog
        assert "mutations" in catalog
        assert "edge_cases" in catalog
        assert "business_profiles" in catalog
        assert "individual_profiles" in catalog
        assert len(catalog["patterns"]) >= 2
        assert len(catalog["mutations"]) >= 1
        assert len(catalog["edge_cases"]) >= 1

    def test_patterns_have_required_fields(self):
        catalog = yaml.safe_load(
            (_SEEDS_DIR / "seed_catalog.yaml").read_text()
        )
        for pattern in catalog["patterns"]:
            assert "ref" in pattern, f"Pattern missing 'ref': {pattern}"
            assert "steps" in pattern, f"Pattern missing 'steps': {pattern}"
            assert "pattern_type" in pattern
