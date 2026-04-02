"""Tests for Step 2.5 — OptionalGroupConfig, flatten_optional_groups, render_mermaid.

Covers: OptionalGroupConfig schema validation, FundsFlowConfig with optional_groups
(step_id uniqueness + depends_on across core + optional), flatten_optional_groups
(activation modes, mutation semantics), render_mermaid (arrow types, opt blocks,
ledger notes, participant resolution), actor_display_name, and passthrough
regression for existing examples and prior step tests.
"""

from __future__ import annotations

import json

import pytest

from flow_compiler import (
    AuthoringConfig,
    FlowIR,
    FlowIRStep,
    LedgerGroup,
    actor_display_name,
    compile_flows,
    compile_to_plan,
    flatten_optional_groups,
    render_mermaid,
)
from models import (
    DataLoaderConfig,
    FundsFlowConfig,
    OptionalGroupConfig,
)
from tests.paths import EXAMPLES_DIR


def _compile(config):
    """Compile a DataLoaderConfig via the pipeline, returning (compiled, flow_irs)."""
    raw = config.model_dump_json().encode()
    plan = compile_to_plan(AuthoringConfig.from_json(raw))
    irs = list(plan.flow_irs) or None
    return plan.config, irs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_config(**kwargs) -> DataLoaderConfig:
    base = {
        "connections": [{"ref": "bank", "entity_id": "example1"}],
        "internal_accounts": [
            {
                "ref": "ops",
                "connection_id": "$ref:connection.bank",
                "name": "Ops",
                "party_name": "Corp",
                "currency": "USD",
            }
        ],
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


def _make_flow_dict(**kwargs) -> dict:
    """Raw dict for a flow — suitable for FundsFlowConfig.model_validate()."""
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
                    {
                        "ledger_account_id": "@actor:direct_1.cash",
                        "amount": 10000,
                        "direction": "debit",
                    },
                    {
                        "ledger_account_id": "@actor:direct_1.revenue",
                        "amount": 10000,
                        "direction": "credit",
                    },
                ],
            },
        ],
    }
    defaults.update(kwargs)
    return defaults


def _make_flow_config(**kwargs) -> FundsFlowConfig:
    return FundsFlowConfig.model_validate(_make_flow_dict(**kwargs))


def _compile_single_flow(**flow_kwargs) -> FlowIR:
    """Compile a single flow and return the FlowIR."""
    config = _make_minimal_config()
    flow = _make_flow_config(**flow_kwargs)
    irs = compile_flows([flow], config)
    assert len(irs) == 1
    return irs[0]


# =========================================================================
# OptionalGroupConfig schema
# =========================================================================


class TestOptionalGroupConfig:
    def test_valid_group(self):
        og = OptionalGroupConfig.model_validate(
            {
                "label": "Return path",
                "trigger": "manual",
                "steps": [
                    {"step_id": "ret", "type": "return", "depends_on": ["deposit"]},
                ],
            }
        )
        assert og.label == "Return path"
        assert og.trigger == "manual"
        assert len(og.steps) == 1

    def test_default_trigger(self):
        og = OptionalGroupConfig.model_validate(
            {
                "label": "Auto",
                "steps": [
                    {"step_id": "x", "type": "return", "depends_on": []},
                ],
            }
        )
        assert og.trigger == "manual"

    def test_system_trigger(self):
        og = OptionalGroupConfig.model_validate(
            {
                "label": "System",
                "trigger": "system",
                "steps": [{"step_id": "x", "type": "return"}],
            }
        )
        assert og.trigger == "system"

    def test_webhook_trigger(self):
        og = OptionalGroupConfig.model_validate(
            {
                "label": "Hook",
                "trigger": "webhook",
                "steps": [{"step_id": "x", "type": "return"}],
            }
        )
        assert og.trigger == "webhook"

    def test_empty_steps_rejected(self):
        with pytest.raises(Exception):
            OptionalGroupConfig.model_validate(
                {
                    "label": "Empty",
                    "steps": [],
                }
            )

    def test_invalid_trigger_rejected(self):
        with pytest.raises(Exception):
            OptionalGroupConfig.model_validate(
                {
                    "label": "Bad",
                    "trigger": "cron",
                    "steps": [{"step_id": "x", "type": "return"}],
                }
            )

    def test_extra_fields_rejected(self):
        with pytest.raises(Exception):
            OptionalGroupConfig.model_validate(
                {
                    "label": "X",
                    "steps": [{"step_id": "x", "type": "return"}],
                    "bogus_field": True,
                }
            )


# =========================================================================
# FundsFlowConfig with optional_groups
# =========================================================================


class TestFundsFlowConfigOptionalGroups:
    def test_empty_optional_groups_backward_compat(self):
        flow = _make_flow_config()
        assert flow.optional_groups == []

    def test_explicit_empty_list(self):
        flow = _make_flow_config(optional_groups=[])
        assert flow.optional_groups == []

    def test_one_optional_group_validates(self):
        flow = _make_flow_config(
            optional_groups=[
                {
                    "label": "Return",
                    "steps": [
                        {"step_id": "ret", "type": "return", "depends_on": ["deposit"]},
                    ],
                }
            ]
        )
        assert len(flow.optional_groups) == 1
        assert flow.optional_groups[0].label == "Return"

    def test_duplicate_step_id_across_core_and_group_raises(self):
        with pytest.raises(Exception, match="Duplicate step_id"):
            _make_flow_config(
                optional_groups=[
                    {
                        "label": "Dup",
                        "steps": [
                            {"step_id": "deposit", "type": "return"},
                        ],
                    }
                ]
            )

    def test_duplicate_step_id_within_optional_group_raises(self):
        with pytest.raises(Exception, match="Duplicate step_id"):
            _make_flow_config(
                optional_groups=[
                    {
                        "label": "Dup",
                        "steps": [
                            {"step_id": "dup_step", "type": "return", "depends_on": ["deposit"]},
                            {"step_id": "dup_step", "type": "return", "depends_on": ["deposit"]},
                        ],
                    }
                ]
            )

    def test_depends_on_core_step_from_optional_group_validates(self):
        flow = _make_flow_config(
            optional_groups=[
                {
                    "label": "Return",
                    "steps": [
                        {"step_id": "ret", "type": "return", "depends_on": ["deposit"]},
                    ],
                }
            ]
        )
        assert flow.optional_groups[0].steps[0].depends_on == ["deposit"]

    def test_depends_on_nonexistent_step_raises(self):
        with pytest.raises(Exception, match="depends_on"):
            _make_flow_config(
                optional_groups=[
                    {
                        "label": "Bad dep",
                        "steps": [
                            {"step_id": "ret", "type": "return", "depends_on": ["ghost"]},
                        ],
                    }
                ]
            )

    def test_cross_group_depends_on_validates(self):
        flow = _make_flow_config(
            optional_groups=[
                {
                    "label": "Group A",
                    "steps": [
                        {"step_id": "step_a", "type": "return", "depends_on": ["deposit"]},
                    ],
                },
                {
                    "label": "Group B",
                    "steps": [
                        {
                            "step_id": "step_b",
                            "type": "ledger_transaction",
                            "depends_on": ["step_a"],
                            "ledger_entries": [
                                {
                                    "ledger_account_id": "@actor:direct_1.cash",
                                    "amount": 10000,
                                    "direction": "debit",
                                },
                                {
                                    "ledger_account_id": "@actor:direct_1.revenue",
                                    "amount": 10000,
                                    "direction": "credit",
                                },
                            ],
                        },
                    ],
                },
            ]
        )
        assert len(flow.optional_groups) == 2


# =========================================================================
# flatten_optional_groups
# =========================================================================


class TestFlattenOptionalGroups:
    def _base_dict(self):
        return {
            "steps": [{"step_id": "core", "type": "payment_order"}],
            "optional_groups": [
                {
                    "label": "return_path",
                    "steps": [{"step_id": "ret", "type": "return"}],
                },
                {
                    "label": "reversal_path",
                    "steps": [{"step_id": "rev", "type": "reversal"}],
                },
            ],
        }

    def test_activated_none_includes_all(self):
        d = self._base_dict()
        result = flatten_optional_groups(d, activated_groups=None)
        step_ids = [s["step_id"] for s in result["steps"]]
        assert step_ids == ["core", "ret", "rev"]

    def test_activated_empty_set_includes_none(self):
        d = self._base_dict()
        result = flatten_optional_groups(d, activated_groups=set())
        step_ids = [s["step_id"] for s in result["steps"]]
        assert step_ids == ["core"]

    def test_activated_specific_group(self):
        d = self._base_dict()
        result = flatten_optional_groups(d, activated_groups={"return_path"})
        step_ids = [s["step_id"] for s in result["steps"]]
        assert step_ids == ["core", "ret"]

    def test_mutates_dict_removes_key(self):
        d = self._base_dict()
        result = flatten_optional_groups(d, activated_groups=None)
        assert result is d
        assert "optional_groups" not in d

    def test_steps_appended_in_group_order(self):
        d = self._base_dict()
        flatten_optional_groups(d, activated_groups=None)
        assert d["steps"][-2]["step_id"] == "ret"
        assert d["steps"][-1]["step_id"] == "rev"

    def test_empty_optional_groups_noop(self):
        d = {"steps": [{"step_id": "a"}], "optional_groups": []}
        flatten_optional_groups(d, activated_groups=None)
        assert len(d["steps"]) == 1

    def test_missing_optional_groups_key_noop(self):
        d = {"steps": [{"step_id": "a"}]}
        flatten_optional_groups(d, activated_groups=None)
        assert len(d["steps"]) == 1


# =========================================================================
# actor_display_name
# =========================================================================


class TestActorDisplayName:
    def test_internal_account_ref(self):
        assert actor_display_name("$ref:internal_account.ops_usd") == "Ops"

    def test_ledger_account_ref(self):
        assert actor_display_name("$ref:ledger_account.cash") == "Cash"

    def test_counterparty_ref(self):
        assert actor_display_name("$ref:counterparty.acme_corp") == "Acme Corp"

    def test_single_segment(self):
        assert actor_display_name("$ref:system") == "System"

    def test_no_ref_prefix(self):
        assert actor_display_name("plain_name") == "Plain Name"


# =========================================================================
# render_mermaid
# =========================================================================


def _build_basic_flow_ir() -> FlowIR:
    """A simple two-step FlowIR: IPD → LT."""
    return FlowIR(
        flow_ref="test_flow",
        instance_id="0000",
        pattern_type="deposit_settle",
        trace_key="deal_id",
        trace_value="deal-test_flow-0",
        trace_metadata={"deal_id": "deal-test_flow-0"},
        steps=[
            FlowIRStep(
                step_id="deposit",
                flow_ref="test_flow",
                instance_id="0000",
                depends_on=[],
                resource_type="incoming_payment_detail",
                payload={
                    "direction": "credit",
                    "amount": 50000,
                    "type": "ach",
                    "internal_account_id": "$ref:internal_account.ops_usd",
                    "metadata": {"deal_id": "deal-test_flow-0"},
                },
                ledger_groups=[],
                trace_metadata={"deal_id": "deal-test_flow-0"},
            ),
            FlowIRStep(
                step_id="settle",
                flow_ref="test_flow",
                instance_id="0000",
                depends_on=["$ref:incoming_payment_detail.test_flow__0000__deposit"],
                resource_type="ledger_transaction",
                payload={
                    "description": "Book deposit",
                    "metadata": {"deal_id": "deal-test_flow-0"},
                },
                ledger_groups=[
                    LedgerGroup(
                        group_id="settle_lg0",
                        inline=False,
                        entries=[
                            {
                                "ledger_account_id": "$ref:ledger_account.cash",
                                "amount": 50000,
                                "direction": "debit",
                            },
                            {
                                "ledger_account_id": "$ref:ledger_account.revenue",
                                "amount": 50000,
                                "direction": "credit",
                            },
                        ],
                        metadata={"deal_id": "deal-test_flow-0"},
                    ),
                ],
                trace_metadata={"deal_id": "deal-test_flow-0"},
            ),
        ],
    )


class TestRenderMermaid:
    def test_starts_with_sequence_diagram(self):
        ir = _build_basic_flow_ir()
        output = render_mermaid(ir)
        assert output.startswith("sequenceDiagram")

    def test_contains_autonumber(self):
        ir = _build_basic_flow_ir()
        output = render_mermaid(ir)
        assert "autonumber" in output

    def test_participants_emitted(self):
        ir = _build_basic_flow_ir()
        output = render_mermaid(ir)
        assert "participant" in output
        assert "External" in output
        assert "Ops" in output

    def test_ipd_sync_arrow(self):
        ir = _build_basic_flow_ir()
        output = render_mermaid(ir)
        assert "->>" in output

    def test_lt_sync_arrow(self):
        ir = _build_basic_flow_ir()
        output = render_mermaid(ir)
        assert "->>" in output

    def test_po_async_arrow(self):
        ir = FlowIR(
            flow_ref="po_flow",
            instance_id="0000",
            pattern_type="payout",
            trace_key="deal_id",
            trace_value="deal-po-0",
            trace_metadata={"deal_id": "deal-po-0"},
            steps=[
                FlowIRStep(
                    step_id="pay",
                    flow_ref="po_flow",
                    instance_id="0000",
                    depends_on=[],
                    resource_type="payment_order",
                    payload={
                        "amount": 25000,
                        "type": "ach",
                        "originating_account_id": "$ref:internal_account.ops_usd",
                        "metadata": {},
                    },
                    ledger_groups=[],
                    trace_metadata={},
                )
            ],
        )
        output = render_mermaid(ir)
        assert "-)" in output
        assert "Ops" in output

    def test_return_dashed_arrow(self):
        ir = FlowIR(
            flow_ref="ret_flow",
            instance_id="0000",
            pattern_type="return",
            trace_key="deal_id",
            trace_value="deal-ret-0",
            trace_metadata={},
            steps=[
                FlowIRStep(
                    step_id="ret",
                    flow_ref="ret_flow",
                    instance_id="0000",
                    depends_on=[],
                    resource_type="return",
                    payload={
                        "internal_account_id": "$ref:internal_account.ops_usd",
                        "metadata": {},
                    },
                    ledger_groups=[],
                    trace_metadata={},
                )
            ],
        )
        output = render_mermaid(ir)
        assert "-->>" in output

    def test_ledger_entries_note_with_dr_cr(self):
        ir = _build_basic_flow_ir()
        output = render_mermaid(ir)
        assert "DR" in output
        assert "CR" in output
        assert "$500.00" in output

    def test_trace_value_in_note(self):
        ir = _build_basic_flow_ir()
        output = render_mermaid(ir)
        assert "deal-test_flow-0" in output

    def test_show_amounts_false_hides_dollars(self):
        ir = _build_basic_flow_ir()
        output = render_mermaid(ir, show_amounts=False)
        assert "$500.00" not in output
        assert "$" not in output

    def test_show_ledger_entries_false_hides_notes(self):
        ir = _build_basic_flow_ir()
        output = render_mermaid(ir, show_ledger_entries=False)
        lines = output.split("\n")
        note_lines = [line for line in lines if "Note over" in line and "DR" in line]
        assert len(note_lines) == 0

    def test_amount_formatting(self):
        ir = _build_basic_flow_ir()
        output = render_mermaid(ir)
        assert "$500.00" in output


# =========================================================================
# render_mermaid with optional_groups (opt blocks)
# =========================================================================


class TestRenderMermaidOptBlocks:
    def _flow_config_with_opt_group(self) -> FundsFlowConfig:
        return _make_flow_config(
            optional_groups=[
                {
                    "label": "Customer requests return",
                    "steps": [
                        {"step_id": "ret", "type": "return", "depends_on": ["deposit"]},
                    ],
                }
            ]
        )

    def _flow_ir_with_opt_steps(self) -> FlowIR:
        """FlowIR that includes steps from both core and optional groups."""
        base = _build_basic_flow_ir()
        base.steps.append(
            FlowIRStep(
                step_id="ret",
                flow_ref="test_flow",
                instance_id="0000",
                depends_on=["$ref:incoming_payment_detail.test_flow__0000__deposit"],
                resource_type="return",
                payload={
                    "internal_account_id": "$ref:internal_account.ops_usd",
                    "metadata": {"deal_id": "deal-test_flow-0"},
                },
                ledger_groups=[],
                trace_metadata={"deal_id": "deal-test_flow-0"},
            )
        )
        return base

    def test_opt_block_emitted(self):
        ir = self._flow_ir_with_opt_steps()
        fc = self._flow_config_with_opt_group()
        output = render_mermaid(ir, flow_config=fc)
        assert "opt Customer requests return" in output

    def test_opt_block_closed(self):
        ir = self._flow_ir_with_opt_steps()
        fc = self._flow_config_with_opt_group()
        output = render_mermaid(ir, flow_config=fc)
        lines = output.strip().split("\n")
        end_count = sum(1 for line in lines if line.strip() == "end")
        assert end_count >= 1

    def test_no_opt_without_flow_config(self):
        ir = self._flow_ir_with_opt_steps()
        output = render_mermaid(ir)
        assert "opt" not in output

    def test_core_steps_not_in_opt(self):
        ir = self._flow_ir_with_opt_steps()
        fc = self._flow_config_with_opt_group()
        output = render_mermaid(ir, flow_config=fc)
        lines = output.split("\n")
        in_opt = False
        core_in_opt = False
        for line in lines:
            if line.strip().startswith("opt "):
                in_opt = True
            elif line.strip() == "end":
                in_opt = False
            elif in_opt and "deposit" in line.lower() and "-)" in line:
                core_in_opt = True
        assert not core_in_opt


# =========================================================================
# End-to-end: demo JSON with optional_groups
# =========================================================================


class TestDemoJsonOptionalGroups:
    def test_demo_json_validates_with_optional_groups(self):
        demo_path = EXAMPLES_DIR / "funds_flow_demo.json"
        data = json.loads(demo_path.read_text())
        config = DataLoaderConfig.model_validate(data)
        assert len(config.funds_flows) == 1
        flow = config.funds_flows[0]
        assert len(flow.optional_groups) == 1
        assert flow.optional_groups[0].label == "Customer requests return"

    def test_demo_compile_pipeline_still_works(self):
        demo_path = EXAMPLES_DIR / "funds_flow_demo.json"
        data = json.loads(demo_path.read_text())
        config = DataLoaderConfig.model_validate(data)
        compiled, _ = _compile(config)
        assert len(compiled.incoming_payment_details) >= 1
        assert len(compiled.ledger_transactions) >= 1

    def test_demo_compile_and_render_mermaid(self):
        demo_path = EXAMPLES_DIR / "funds_flow_demo.json"
        data = json.loads(demo_path.read_text())
        config = DataLoaderConfig.model_validate(data)
        flow_irs = compile_flows(config.funds_flows, config)
        assert len(flow_irs) == 1
        output = render_mermaid(flow_irs[0], flow_config=config.funds_flows[0])
        assert output.startswith("sequenceDiagram")
        assert "autonumber" in output

    def test_demo_flatten_then_compile_includes_return(self):
        """Flatten all groups, then compile — return step appears."""
        demo_path = EXAMPLES_DIR / "funds_flow_demo.json"
        data = json.loads(demo_path.read_text())
        flow_dict = data["funds_flows"][0].copy()
        flatten_optional_groups(flow_dict, activated_groups=None)
        step_ids = [s["step_id"] for s in flow_dict["steps"]]
        assert "return_deposit" in step_ids
        assert "reverse_settle" in step_ids

    def test_demo_flatten_happy_path_excludes_return(self):
        """Flatten with empty set — only happy path steps remain."""
        demo_path = EXAMPLES_DIR / "funds_flow_demo.json"
        data = json.loads(demo_path.read_text())
        flow_dict = data["funds_flows"][0].copy()
        flatten_optional_groups(flow_dict, activated_groups=set())
        step_ids = [s["step_id"] for s in flow_dict["steps"]]
        assert "return_deposit" not in step_ids
        assert step_ids == ["deposit", "settle", "post_settle"]


# =========================================================================
# Passthrough regression — existing examples still validate
# =========================================================================


class TestPassthroughRegression:
    @pytest.mark.parametrize("filename", sorted(p.name for p in EXAMPLES_DIR.glob("*.json")))
    def test_example_validates(self, filename):
        path = EXAMPLES_DIR / filename
        data = json.loads(path.read_text())
        config = DataLoaderConfig.model_validate(data)
        compiled, _ = _compile(config)
        assert isinstance(compiled, DataLoaderConfig)
