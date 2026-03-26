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
    IncomingPaymentDetailStep,
    LedgerTransactionStep,
    PaymentOrderStep,
    ReturnConfig,
    ReturnStep,
    ReversalStep,
    TransitionLedgerTransactionStep,
    VALID_STEP_TYPES,
)
from flow_compiler import AuthoringConfig, compile_to_plan


def _compile(config):
    """Compile a DataLoaderConfig via the pipeline, returning (compiled, flow_irs)."""
    raw = config.model_dump_json().encode()
    plan = compile_to_plan(AuthoringConfig.from_json(raw))
    irs = list(plan.flow_irs) or None
    return plan.config, irs


def _ipd(**overrides):
    """Minimal valid IPD step dict for flow construction."""
    d = {
        "step_id": "s1",
        "type": "incoming_payment_detail",
        "payment_type": "ach",
        "amount": 1000,
        "internal_account_id": "ia_ops",
    }
    d.update(overrides)
    return d


def _lt(**overrides):
    """Minimal valid LT step dict for flow construction."""
    d = {
        "step_id": "s1",
        "type": "ledger_transaction",
        "ledger_entries": [
            {"amount": 100, "direction": "debit", "ledger_account_id": "la_a"},
            {"amount": 100, "direction": "credit", "ledger_account_id": "la_b"},
        ],
    }
    d.update(overrides)
    return d


# ---------------------------------------------------------------------------
# Typed Step Models (Plan 0: Discriminated Union)
# ---------------------------------------------------------------------------


class TestTypedStepModels:
    def test_ipd_requires_payment_type(self):
        with pytest.raises(Exception, match="payment_type"):
            FundsFlowStepConfig(
                step_id="dep", type="incoming_payment_detail",
                amount=5000, internal_account_id="ia",
            )

    def test_ipd_requires_internal_account(self):
        with pytest.raises(Exception, match="internal_account_id"):
            FundsFlowStepConfig(
                step_id="dep", type="incoming_payment_detail",
                payment_type="ach", amount=5000,
            )

    def test_po_requires_direction(self):
        with pytest.raises(Exception, match="direction"):
            FundsFlowStepConfig(
                step_id="pay", type="payment_order",
                payment_type="ach", amount=5000,
                originating_account_id="ia",
            )

    def test_lt_requires_entries(self):
        with pytest.raises(Exception, match="ledger_entries"):
            FundsFlowStepConfig(step_id="lt", type="ledger_transaction")

    def test_tlt_requires_status(self):
        with pytest.raises(Exception, match="status"):
            FundsFlowStepConfig(
                step_id="t", type="transition_ledger_transaction",
            )

    def test_valid_ipd(self):
        step = FundsFlowStepConfig(
            step_id="dep", type="incoming_payment_detail",
            payment_type="ach", amount=50000,
            internal_account_id="$ref:internal_account.ops",
        )
        assert step.step_id == "dep"
        assert step.type == "incoming_payment_detail"
        assert isinstance(step, IncomingPaymentDetailStep)

    def test_ipd_direction_defaults_to_credit(self):
        step = FundsFlowStepConfig(
            step_id="dep", type="incoming_payment_detail",
            payment_type="ach", amount=1000,
            internal_account_id="ia",
        )
        assert step.direction == "credit"

    def test_ipd_rejects_debit_direction(self):
        with pytest.raises(Exception):
            FundsFlowStepConfig(
                step_id="dep", type="incoming_payment_detail",
                payment_type="ach", amount=1000,
                internal_account_id="ia", direction="debit",
            )

    def test_extra_fields_forbidden(self):
        with pytest.raises(Exception, match="Extra inputs"):
            FundsFlowStepConfig(
                step_id="dep", type="incoming_payment_detail",
                payment_type="ach", amount=1000,
                internal_account_id="ia", bogus_field="nope",
            )

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

    def test_invalid_step_type(self):
        with pytest.raises(Exception):
            FundsFlowStepConfig(step_id="bad", type="not_a_real_type")

    def test_valid_step_types_complete(self):
        assert VALID_STEP_TYPES == frozenset({
            "payment_order", "incoming_payment_detail",
            "ledger_transaction", "expected_payment",
            "return", "reversal", "transition_ledger_transaction",
            "verify_external_account", "complete_verification",
            "archive_resource",
        })

    def test_return_code_defaults_to_r01(self):
        step = FundsFlowStepConfig(step_id="ret", type="return")
        assert isinstance(step, ReturnStep)
        assert step.code == "R01"

    def test_return_code_custom(self):
        step = FundsFlowStepConfig(step_id="ret", type="return", code="R05")
        assert isinstance(step, ReturnStep)
        assert step.code == "R05"

    def test_reversal_reason_defaults_to_duplicate(self):
        step = FundsFlowStepConfig(step_id="rev", type="reversal")
        assert isinstance(step, ReversalStep)
        assert step.reason == "duplicate"

    def test_union_parses_all_types(self):
        """Each step type can be parsed via the compat factory."""
        specs = [
            {"step_id": "a", "type": "payment_order", "payment_type": "ach",
             "direction": "debit", "amount": 100, "originating_account_id": "ia"},
            {"step_id": "b", "type": "incoming_payment_detail",
             "payment_type": "ach", "amount": 100, "internal_account_id": "ia"},
            {"step_id": "c", "type": "expected_payment"},
            {"step_id": "d", "type": "ledger_transaction", "ledger_entries": [
                {"amount": 1, "direction": "debit", "ledger_account_id": "la"},
                {"amount": 1, "direction": "credit", "ledger_account_id": "lb"},
            ]},
            {"step_id": "e", "type": "return"},
            {"step_id": "f", "type": "reversal"},
            {"step_id": "g", "type": "transition_ledger_transaction", "status": "posted"},
        ]
        for spec in specs:
            step = FundsFlowStepConfig.model_validate(spec)
            assert step.type == spec["type"]


# ---------------------------------------------------------------------------
# FundsFlowConfig
# ---------------------------------------------------------------------------


class TestFundsFlowConfig:
    def test_valid_flow(self):
        flow = FundsFlowConfig(
            ref="test_flow",
            pattern_type="deposit_settle",
            steps=[_ipd()],
        )
        assert flow.ref == "test_flow"
        assert len(flow.steps) == 1

    def test_duplicate_step_ids(self):
        with pytest.raises(ValueError, match="Duplicate step_id"):
            FundsFlowConfig(
                ref="bad_flow",
                pattern_type="test",
                steps=[_ipd(), _lt(step_id="s1")],
            )

    def test_invalid_depends_on(self):
        with pytest.raises(ValueError, match="not a valid step_id"):
            FundsFlowConfig(
                ref="bad_flow",
                pattern_type="test",
                steps=[_lt(depends_on=["nonexistent"])],
            )

    def test_bad_trace_placeholder(self):
        with pytest.raises(ValueError, match="unknown placeholders"):
            FundsFlowConfig(
                ref="bad",
                pattern_type="test",
                trace_value_template="{ref}-{bad_key}",
                steps=[_ipd()],
            )

    def test_empty_steps_rejected(self):
        with pytest.raises(ValueError):
            FundsFlowConfig(ref="empty", pattern_type="test", steps=[])

    def test_valid_depends_on(self):
        flow = FundsFlowConfig(
            ref="chained",
            pattern_type="test",
            steps=[_ipd(), _lt(step_id="s2", depends_on=["s1"])],
        )
        assert flow.steps[1].depends_on == ["s1"]

    def test_default_trace_template(self):
        flow = FundsFlowConfig(
            ref="f1",
            pattern_type="test",
            steps=[_ipd()],
        )
        assert flow.trace_value_template == "{ref}-{instance}"
        assert flow.trace_key == "deal_id"

    def test_actors_and_metadata(self):
        flow = FundsFlowConfig(
            ref="f1",
            pattern_type="test",
            actors={
                "direct_1": {
                    "alias": "Platform",
                    "frame_type": "direct",
                    "customer_name": "Platform",
                    "slots": {"ops": "$ref:internal_account.ops"},
                }
            },
            trace_metadata={"env": "demo"},
            steps=[_ipd()],
        )
        frame = flow.actors["direct_1"]
        assert frame.alias == "Platform"
        assert frame.frame_type == "direct"
        assert frame.slots["ops"].ref == "$ref:internal_account.ops"
        assert flow.trace_metadata == {"env": "demo"}

    def test_scale_config(self):
        flow = FundsFlowConfig(
            ref="f1",
            pattern_type="test",
            scale=FundsFlowScaleConfig(instances=100),
            steps=[_ipd()],
        )
        assert flow.scale.instances == 100

    def test_extra_fields_forbidden_on_flow(self):
        with pytest.raises(ValueError):
            FundsFlowConfig(
                ref="f1",
                pattern_type="test",
                bogus_field="nope",
                steps=[_ipd()],
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
                    "steps": [_ipd()],
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
                    "steps": [_ipd()],
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
# compile_to_plan gate
# ---------------------------------------------------------------------------


class TestCompilePipeline:
    def test_passthrough_no_flows(self):
        config = DataLoaderConfig()
        result, irs = _compile(config)
        assert irs is None
        assert result.funds_flows == []

    def test_passthrough_with_resources_no_flows(self):
        config = DataLoaderConfig(
            ledgers=[{"ref": "main", "name": "Main"}],
        )
        result, irs = _compile(config)
        assert irs is None
        assert len(result.ledgers) == 1

    def test_compiles_with_flows(self):
        config = DataLoaderConfig(
            funds_flows=[
                {
                    "ref": "f1",
                    "pattern_type": "deposit",
                    "steps": [
                        {
                            "step_id": "s1",
                            "type": "incoming_payment_detail",
                            "payment_type": "ach",
                            "direction": "credit",
                            "amount": 1000,
                            "internal_account_id": "$ref:internal_account.ops",
                        }
                    ],
                }
            ],
        )
        result, _ = _compile(config)
        assert result.funds_flows == []
        assert len(result.incoming_payment_details) == 1


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
            result, irs = _compile(config)
            assert irs is None


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
        assert len(config.funds_flows[0].steps) == 3

    def test_demo_compiles_end_to_end(self):
        demo = _EXAMPLE_DIR / "funds_flow_demo.json"
        if not demo.exists():
            pytest.skip("funds_flow_demo.json not yet created")
        config = DataLoaderConfig.model_validate_json(demo.read_bytes())
        result, _ = _compile(config)
        assert result.funds_flows == []
        assert len(result.incoming_payment_details) == 1
        assert len(result.ledger_transactions) == 1


# ---------------------------------------------------------------------------
# Seed catalog
# ---------------------------------------------------------------------------

_SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds"


class TestSeedCatalog:
    def test_curated_yamls_load(self):
        for name in ["harry_potter", "superheroes", "seinfeld"]:
            catalog = yaml.safe_load(
                (_SEEDS_DIR / f"{name}.yaml").read_text()
            )
            assert "business_profiles" in catalog
            assert "individual_profiles" in catalog
            assert len(catalog["business_profiles"]) >= 50

    def test_industry_templates_load(self):
        templates = yaml.safe_load(
            (_SEEDS_DIR / "industry_templates.yaml").read_text()
        )
        for key in ["tech", "government", "payroll", "manufacturing", "property_management", "construction"]:
            assert key in templates, f"Missing industry vertical: {key}"
            assert len(templates[key]["company_patterns"]) >= 5
