"""Tests for Step 2s — FlowIR compiler + emitter.

Covers: resolve_actors, expand_trace_value, _validate_ref_segment,
compile_flows (two-pass, forward deps, actor resolution, trace metadata,
payment_type mapping, auto-derive lifecycle refs), emit_dataloader_config
(LT steps, non-LT steps, standalone LT, inject lifecycle depends_on),
end-to-end with funds_flow_demo.json, and passthrough regression for
existing examples.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flow_compiler import (
    AuthoringConfig,
    FlowIR,
    FlowIRStep,
    LedgerGroup,
    _auto_derive_lifecycle_refs,
    _validate_ref_segment,
    _with_lifecycle_depends_on,
    compile_flows,
    compile_to_plan,
    emit_dataloader_config,
    expand_trace_value,
    resolve_actors,
)


def _compile(config):
    """Compile a DataLoaderConfig via the pipeline, returning (compiled, flow_irs)."""
    raw = config.model_dump_json().encode()
    plan = compile_to_plan(AuthoringConfig.from_json(raw))
    irs = list(plan.flow_irs) or None
    return plan.config, irs
from models import (
    DataLoaderConfig,
    FundsFlowConfig,
    IncomingPaymentDetailStep,
    PaymentOrderStep,
    ReturnStep,
    ReversalStep,
)

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


# =========================================================================
# resolve_actors
# =========================================================================


class TestResolveActors:
    def test_simple_replacement(self):
        actor_refs = {"direct_1.ops": "$ref:internal_account.ops_usd"}
        assert resolve_actors("@actor:direct_1.ops", actor_refs) == "$ref:internal_account.ops_usd"

    def test_unknown_alias_raises(self):
        with pytest.raises(ValueError, match="Unknown actor ref 'nope'"):
            resolve_actors("@actor:nope", {"direct_1.ops": "$ref:ia.ops"})

    def test_nested_dict(self):
        actor_refs = {"direct_1.a": "$ref:x.y"}
        result = resolve_actors({"key": "@actor:direct_1.a", "other": "plain"}, actor_refs)
        assert result == {"key": "$ref:x.y", "other": "plain"}

    def test_nested_list(self):
        actor_refs = {"direct_1.a": "$ref:x.y"}
        result = resolve_actors(["@actor:direct_1.a", "plain", 42], actor_refs)
        assert result == ["$ref:x.y", "plain", 42]

    def test_deeply_nested(self):
        actor_refs = {"direct_1.a": "$ref:x.y"}
        result = resolve_actors({"l": [{"v": "@actor:direct_1.a"}]}, actor_refs)
        assert result == {"l": [{"v": "$ref:x.y"}]}

    def test_non_actor_string_passthrough(self):
        assert resolve_actors("$ref:ledger.main", {}) == "$ref:ledger.main"
        assert resolve_actors("plain", {}) == "plain"

    def test_non_string_passthrough(self):
        assert resolve_actors(42, {}) == 42
        assert resolve_actors(None, {}) is None


# =========================================================================
# expand_trace_value
# =========================================================================


class TestExpandTraceValue:
    def test_basic(self):
        assert expand_trace_value("{ref}-{instance}", "flow", 0) == "flow-0"

    def test_ref_only(self):
        assert expand_trace_value("deal-{ref}", "abc", 5) == "deal-abc"

    def test_instance_only(self):
        assert expand_trace_value("batch-{instance}", "x", 42) == "batch-42"

    def test_no_placeholders(self):
        assert expand_trace_value("static-value", "x", 0) == "static-value"

    def test_unknown_placeholder_empties(self):
        assert expand_trace_value("{ref}-{bad}", "x", 0) == "x-"

    def test_profile_placeholders(self):
        assert expand_trace_value(
            "{ref}-{business_name}", "flow", 0, profile={"business_name": "Acme"}
        ) == "flow-Acme"

    def test_profile_missing_key_empties(self):
        assert expand_trace_value("{business_name}-{industry}", "x", 0) == "-"


# =========================================================================
# _validate_ref_segment
# =========================================================================


class TestValidateRefSegment:
    def test_clean_segment(self):
        _validate_ref_segment("simple_deposit")
        _validate_ref_segment("step1")

    def test_double_underscore_raises(self):
        with pytest.raises(ValueError, match="must not contain '__'"):
            _validate_ref_segment("bad__segment")


# =========================================================================
# compile_flows
# =========================================================================


def _make_minimal_config(**kwargs) -> DataLoaderConfig:
    """Build a DataLoaderConfig with minimal required fields + overrides."""
    base = {
        "connections": [{"ref": "bank", "entity_id": "example1"}],
        "internal_accounts": [{
            "ref": "ops",
            "connection_id": "$ref:connection.bank",
            "name": "Ops",
            "party_name": "Corp",
            "currency": "USD",
        }],
        "ledgers": [{"ref": "main", "name": "Main"}],
        "ledger_accounts": [
            {
                "ref": "cash",
                "ledger_id": "$ref:ledger.main",
                "name": "Cash",
                "normal_balance": "debit",
                "currency": "USD",
            },
            {
                "ref": "revenue",
                "ledger_id": "$ref:ledger.main",
                "name": "Revenue",
                "normal_balance": "credit",
                "currency": "USD",
            },
        ],
    }
    base.update(kwargs)
    return DataLoaderConfig.model_validate(base)


def _make_flow_config(**kwargs) -> FundsFlowConfig:
    """Build a FundsFlowConfig with sensible defaults."""
    defaults = {
        "ref": "test_flow",
        "pattern_type": "test",
        "trace_key": "deal_id",
        "trace_value_template": "{ref}-{instance}",
        "actors": {
            "direct_1": {
                "alias": "Platform",
                "frame_type": "direct",
                "customer_name": "Platform",
                "slots": {
                    "ops": "$ref:internal_account.ops",
                    "cash": "$ref:ledger_account.cash",
                    "revenue": "$ref:ledger_account.revenue",
                },
            },
        },
        "steps": [
            {
                "step_id": "deposit",
                "type": "incoming_payment_detail",
                "payment_type": "ach",
                "direction": "credit",
                "amount": 10000,
                "internal_account_id": "@actor:direct_1.ops",
            },
            {
                "step_id": "settle",
                "type": "ledger_transaction",
                "depends_on": ["deposit"],
                "description": "Book deposit",
                "ledger_entries": [
                    {"ledger_account_id": "@actor:direct_1.cash", "amount": 10000, "direction": "debit"},
                    {"ledger_account_id": "@actor:direct_1.revenue", "amount": 10000, "direction": "credit"},
                ],
            },
        ],
    }
    defaults.update(kwargs)
    return FundsFlowConfig.model_validate(defaults)


class TestCompileFlows:
    def test_basic_two_step_flow(self):
        config = _make_minimal_config()
        flow = _make_flow_config()
        irs = compile_flows([flow], config)

        assert len(irs) == 1
        ir = irs[0]
        assert ir.flow_ref == "test_flow"
        assert ir.instance_id == "0000"
        assert len(ir.steps) == 2

    def test_step_refs_follow_naming_convention(self):
        config = _make_minimal_config()
        flow = _make_flow_config()
        irs = compile_flows([flow], config)
        step = irs[0].steps[0]
        assert step.emitted_ref == "test_flow__0000__deposit"

    def test_depends_on_maps_to_typed_refs(self):
        config = _make_minimal_config()
        flow = _make_flow_config()
        irs = compile_flows([flow], config)
        settle_step = irs[0].steps[1]
        assert settle_step.depends_on == (
            "$ref:incoming_payment_detail.test_flow__0000__deposit",
        )

    def test_forward_depends_on_resolved(self):
        """Steps in non-topological order — forward deps must still resolve."""
        config = _make_minimal_config()
        flow = _make_flow_config(steps=[
            {
                "step_id": "settle",
                "type": "ledger_transaction",
                "depends_on": ["deposit"],
                "ledger_entries": [
                    {"ledger_account_id": "@actor:direct_1.cash", "amount": 5000, "direction": "debit"},
                    {"ledger_account_id": "@actor:direct_1.revenue", "amount": 5000, "direction": "credit"},
                ],
            },
            {
                "step_id": "deposit",
                "type": "incoming_payment_detail",
                "payment_type": "ach",
                "direction": "credit",
                "amount": 5000,
                "internal_account_id": "@actor:direct_1.ops",
            },
        ])
        irs = compile_flows([flow], config)
        settle_step = irs[0].steps[0]
        assert "$ref:incoming_payment_detail.test_flow__0000__deposit" in settle_step.depends_on

    def test_actor_refs_resolved_in_payload(self):
        config = _make_minimal_config()
        flow = _make_flow_config()
        irs = compile_flows([flow], config)
        ipd_step = irs[0].steps[0]
        assert ipd_step.payload["internal_account_id"] == "$ref:internal_account.ops"

    def test_actor_refs_resolved_in_ledger_entries(self):
        config = _make_minimal_config()
        flow = _make_flow_config()
        irs = compile_flows([flow], config)
        lt_step = irs[0].steps[1]
        entries = lt_step.ledger_groups[0].entries
        assert entries[0]["ledger_account_id"] == "$ref:ledger_account.cash"
        assert entries[1]["ledger_account_id"] == "$ref:ledger_account.revenue"

    def test_trace_metadata_stamped(self):
        config = _make_minimal_config()
        flow = _make_flow_config()
        irs = compile_flows([flow], config)
        for step in irs[0].steps:
            assert step.trace_metadata["deal_id"] == "test_flow-0"
            assert step.payload["metadata"]["deal_id"] == "test_flow-0"

    def test_payment_type_mapped_to_type(self):
        config = _make_minimal_config()
        flow = _make_flow_config()
        irs = compile_flows([flow], config)
        ipd_step = irs[0].steps[0]
        assert ipd_step.payload.get("type") == "ach"
        assert "payment_type" not in ipd_step.payload

    def test_missing_payment_type_on_ipd_raises(self):
        """payment_type is now required at parse time by IncomingPaymentDetailStep."""
        with pytest.raises(Exception, match="payment_type"):
            FundsFlowConfig.model_validate({
                "ref": "test",
                "pattern_type": "test",
                "steps": [{
                    "step_id": "deposit",
                    "type": "incoming_payment_detail",
                    "direction": "credit",
                    "amount": 1000,
                    "internal_account_id": "ia",
                }],
            })

    def test_lt_step_has_ledger_group(self):
        config = _make_minimal_config()
        flow = _make_flow_config()
        irs = compile_flows([flow], config)
        lt_step = irs[0].steps[1]
        assert len(lt_step.ledger_groups) == 1
        assert lt_step.ledger_groups[0].group_id == "settle_lg0"

    def test_validate_ref_segment_rejects_bad_flow_ref(self):
        config = _make_minimal_config()
        flow = _make_flow_config(ref="bad__ref")
        with pytest.raises(ValueError, match="must not contain '__'"):
            compile_flows([flow], config)

    def test_validate_ref_segment_rejects_bad_step_id(self):
        config = _make_minimal_config()
        flow = _make_flow_config(steps=[
            {
                "step_id": "bad__step",
                "type": "incoming_payment_detail",
                "payment_type": "ach",
                "direction": "credit",
                "amount": 1000,
                "internal_account_id": "@actor:direct_1.ops",
            },
        ])
        with pytest.raises(ValueError, match="must not contain '__'"):
            compile_flows([flow], config)


# =========================================================================
# Auto-derive lifecycle refs
# =========================================================================


class TestAutoDerive:
    def test_return_gets_returnable_id(self):
        steps = [
            IncomingPaymentDetailStep(
                step_id="dep", type="incoming_payment_detail",
                payment_type="ach", amount=1000, internal_account_id="@actor:direct_1.ops",
            ),
            ReturnStep(step_id="ret", type="return", depends_on=["dep"]),
        ]
        step_ref_map = {
            "dep": "$ref:incoming_payment_detail.flow__0000__dep",
            "ret": "$ref:return.flow__0000__ret",
        }
        step_dict: dict = {}
        _auto_derive_lifecycle_refs(steps[1], step_dict, step_ref_map, steps)
        assert step_dict["returnable_id"] == "$ref:incoming_payment_detail.flow__0000__dep"

    def test_return_does_not_overwrite_explicit(self):
        steps = [
            IncomingPaymentDetailStep(
                step_id="dep", type="incoming_payment_detail",
                payment_type="ach", amount=1000, internal_account_id="@actor:direct_1.ops",
            ),
            ReturnStep(
                step_id="ret", type="return", depends_on=["dep"],
                returnable_id="$ref:ipd.manual",
            ),
        ]
        step_ref_map = {
            "dep": "$ref:incoming_payment_detail.flow__0000__dep",
            "ret": "$ref:return.flow__0000__ret",
        }
        step_dict: dict = {"returnable_id": "$ref:ipd.manual"}
        _auto_derive_lifecycle_refs(steps[1], step_dict, step_ref_map, steps)
        assert step_dict["returnable_id"] == "$ref:ipd.manual"

    def test_reversal_gets_payment_order_id(self):
        steps = [
            PaymentOrderStep(
                step_id="pay", type="payment_order",
                payment_type="ach", direction="debit", amount=5000,
                originating_account_id="@actor:direct_1.ops",
            ),
            ReversalStep(step_id="rev", type="reversal", depends_on=["pay"]),
        ]
        step_ref_map = {
            "pay": "$ref:payment_order.flow__0000__pay",
            "rev": "$ref:reversal.flow__0000__rev",
        }
        step_dict: dict = {}
        _auto_derive_lifecycle_refs(steps[1], step_dict, step_ref_map, steps)
        assert step_dict["payment_order_id"] == "$ref:payment_order.flow__0000__pay"


# =========================================================================
# _inject_lifecycle_depends_on
# =========================================================================


class TestWithLifecycleDependsOn:
    def test_return_step(self):
        step = FlowIRStep(
            step_id="ret", flow_ref="f", instance_id="0000",
            depends_on=(), resource_type="return",
            payload={"returnable_id": "$ref:incoming_payment_detail.f__0000__dep"},
            ledger_groups=(), trace_metadata={},
        )
        step = _with_lifecycle_depends_on(step)
        assert "$ref:incoming_payment_detail.f__0000__dep" in step.depends_on

    def test_reversal_step(self):
        step = FlowIRStep(
            step_id="rev", flow_ref="f", instance_id="0000",
            depends_on=(), resource_type="reversal",
            payload={"payment_order_id": "$ref:payment_order.f__0000__pay"},
            ledger_groups=(), trace_metadata={},
        )
        step = _with_lifecycle_depends_on(step)
        assert "$ref:payment_order.f__0000__pay" in step.depends_on

    def test_no_duplicate_deps(self):
        step = FlowIRStep(
            step_id="ret", flow_ref="f", instance_id="0000",
            depends_on=("$ref:incoming_payment_detail.f__0000__dep",),
            resource_type="return",
            payload={"returnable_id": "$ref:incoming_payment_detail.f__0000__dep"},
            ledger_groups=(), trace_metadata={},
        )
        step = _with_lifecycle_depends_on(step)
        assert step.depends_on.count("$ref:incoming_payment_detail.f__0000__dep") == 1

    def test_other_type_unchanged(self):
        step = FlowIRStep(
            step_id="ipd", flow_ref="f", instance_id="0000",
            depends_on=(), resource_type="incoming_payment_detail",
            payload={"internal_account_id": "$ref:ia.ops"},
            ledger_groups=(), trace_metadata={},
        )
        step = _with_lifecycle_depends_on(step)
        assert step.depends_on == ()


# =========================================================================
# emit_dataloader_config
# =========================================================================


class TestEmitDataloaderConfig:
    def _compile_default(self):
        config = _make_minimal_config()
        flow = _make_flow_config()
        irs = compile_flows([flow], config)
        return emit_dataloader_config(irs, config), config

    def test_funds_flows_cleared(self):
        emitted, _ = self._compile_default()
        assert emitted.funds_flows == []

    def test_ipd_emitted(self):
        emitted, _ = self._compile_default()
        ipd_refs = [r.ref for r in emitted.incoming_payment_details]
        assert "test_flow__0000__deposit" in ipd_refs

    def test_lt_emitted(self):
        emitted, _ = self._compile_default()
        lt_refs = [r.ref for r in emitted.ledger_transactions]
        assert "test_flow__0000__settle" in lt_refs

    def test_lt_step_has_entries_directly(self):
        emitted, _ = self._compile_default()
        lt = next(r for r in emitted.ledger_transactions
                  if r.ref == "test_flow__0000__settle")
        assert len(lt.ledger_entries) == 2
        entry_dirs = {e.direction for e in lt.ledger_entries}
        assert entry_dirs == {"debit", "credit"}

    def test_lt_step_depends_on_ipd(self):
        emitted, _ = self._compile_default()
        lt = next(r for r in emitted.ledger_transactions
                  if r.ref == "test_flow__0000__settle")
        assert "$ref:incoming_payment_detail.test_flow__0000__deposit" in lt.depends_on

    def test_metadata_stamped_on_ipd(self):
        emitted, _ = self._compile_default()
        ipd = next(r for r in emitted.incoming_payment_details
                   if r.ref == "test_flow__0000__deposit")
        assert ipd.metadata["deal_id"] == "test_flow-0"

    def test_metadata_stamped_on_lt(self):
        emitted, _ = self._compile_default()
        lt = next(r for r in emitted.ledger_transactions
                  if r.ref == "test_flow__0000__settle")
        assert lt.metadata["deal_id"] == "test_flow-0"

    def test_base_resources_preserved(self):
        emitted, _ = self._compile_default()
        assert len(emitted.connections) == 1
        assert emitted.connections[0].ref == "bank"
        assert len(emitted.internal_accounts) == 1
        assert len(emitted.ledgers) == 1
        assert len(emitted.ledger_accounts) == 2

    def test_output_validates_against_pydantic(self):
        """The emitted output must pass DataLoaderConfig validation."""
        emitted, _ = self._compile_default()
        reloaded = DataLoaderConfig.model_validate(
            emitted.model_dump(exclude_none=True)
        )
        assert len(reloaded.incoming_payment_details) == len(emitted.incoming_payment_details)


# =========================================================================
# End-to-end: funds_flow_demo.json
# =========================================================================


class TestEndToEnd:
    def test_demo_json_compiles(self):
        raw = (EXAMPLES_DIR / "funds_flow_demo.json").read_text()
        config = DataLoaderConfig.model_validate_json(raw)
        assert len(config.funds_flows) == 1

        compiled, _ = _compile(config)
        assert compiled.funds_flows == []
        assert len(compiled.incoming_payment_details) == 1
        assert len(compiled.ledger_transactions) == 1

    def test_demo_json_ipd_has_correct_type(self):
        raw = (EXAMPLES_DIR / "funds_flow_demo.json").read_text()
        compiled, _ = _compile(DataLoaderConfig.model_validate_json(raw))
        ipd = compiled.incoming_payment_details[0]
        assert ipd.type == "ach"

    def test_demo_json_trace_metadata(self):
        raw = (EXAMPLES_DIR / "funds_flow_demo.json").read_text()
        compiled, _ = _compile(DataLoaderConfig.model_validate_json(raw))
        ipd = compiled.incoming_payment_details[0]
        assert "deal_id" in ipd.metadata
        assert ipd.metadata["deal_id"] == "deal-simple_deposit-0"

    def test_demo_json_dry_run(self):
        """Compiled output produces valid batches via dry_run."""
        from engine import dry_run

        raw = (EXAMPLES_DIR / "funds_flow_demo.json").read_text()
        compiled, _ = _compile(DataLoaderConfig.model_validate_json(raw))
        batches = dry_run(compiled)
        assert len(batches) > 0
        all_refs = [ref for batch in batches for ref in batch]
        assert any("incoming_payment_detail" in r for r in all_refs)
        assert any("ledger_transaction" in r for r in all_refs)

    def test_demo_json_round_trip(self):
        raw = (EXAMPLES_DIR / "funds_flow_demo.json").read_text()
        compiled, _ = _compile(DataLoaderConfig.model_validate_json(raw))
        dumped = compiled.model_dump_json(exclude_none=True)
        recompiled, _ = _compile(DataLoaderConfig.model_validate_json(dumped))
        assert len(recompiled.incoming_payment_details) == len(compiled.incoming_payment_details)
        assert len(recompiled.ledger_transactions) == len(compiled.ledger_transactions)


# =========================================================================
# Passthrough regression
# =========================================================================


class TestPassthroughRegression:
    def test_passthrough_no_funds_flows(self):
        config = _make_minimal_config()
        result, irs = _compile(config)
        assert irs is None

    def test_existing_examples_passthrough(self):
        """Examples without funds_flows compile unchanged; those with funds_flows produce FlowIRs."""
        for path in EXAMPLES_DIR.glob("*.json"):
            raw = path.read_text()
            try:
                config = DataLoaderConfig.model_validate_json(raw)
            except Exception:
                continue
            result, flow_irs = _compile(config)
            if config.funds_flows:
                assert flow_irs is not None, f"{path.name} should compile"
            else:
                assert flow_irs is None, f"{path.name} should have no flow_irs"
