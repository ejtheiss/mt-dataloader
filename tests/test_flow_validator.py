"""Tests for flow_validator.py — advisory diagnostic rules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flow_compiler import FlowIR, FlowIRStep, LedgerGroup, compile_flows, flatten_actor_refs
from flow_validator import (
    DEFAULT_RULES,
    FlowDiagnostic,
    FlowValidator,
    validate_flow,
)
from models import ActorFrame, DataLoaderConfig

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _ir(steps=(), **kw):
    defaults = dict(
        flow_ref="test", instance_id="0000", pattern_type="test",
        trace_key="id", trace_value="TEST-0000", trace_metadata={},
    )
    defaults.update(kw)
    defaults["steps"] = tuple(steps)
    return FlowIR(**defaults)


def _step(step_id="s1", rtype="payment_order", payload=None, ledger_groups=(), depends_on=()):
    return FlowIRStep(
        step_id=step_id, flow_ref="test", instance_id="0000",
        depends_on=tuple(depends_on), resource_type=rtype,
        payload=payload or {}, ledger_groups=tuple(ledger_groups),
        trace_metadata={},
    )


def _lg(group_id="lg0", entries=(), inline=False, status=None):
    return LedgerGroup(
        group_id=group_id, inline=inline,
        entries=tuple(entries), metadata={}, status=status,
    )


class TestLedgerBalanceRule:
    def test_balanced_entries_no_diagnostic(self):
        ir = _ir([_step("lt1", "ledger_transaction", ledger_groups=[
            _lg(entries=[
                {"ledger_account_id": "a", "direction": "debit", "amount": 100},
                {"ledger_account_id": "b", "direction": "credit", "amount": 100},
            ]),
        ])])
        diags = validate_flow(ir)
        assert not any(d.rule_id == "LEDGER_001" for d in diags)

    def test_imbalanced_entries_triggers(self):
        ir = _ir([_step("lt1", "ledger_transaction", ledger_groups=[
            _lg(entries=[
                {"ledger_account_id": "a", "direction": "debit", "amount": 100},
                {"ledger_account_id": "b", "direction": "credit", "amount": 50},
            ]),
        ])])
        diags = validate_flow(ir)
        errors = [d for d in diags if d.rule_id == "LEDGER_001"]
        assert len(errors) == 1
        assert errors[0].severity == "error"


class TestSelfDebitRule:
    def test_same_account_both_sides(self):
        ir = _ir([_step("lt1", "ledger_transaction", ledger_groups=[
            _lg(entries=[
                {"ledger_account_id": "acct_x", "direction": "debit", "amount": 50},
                {"ledger_account_id": "acct_x", "direction": "credit", "amount": 50},
            ]),
        ])])
        diags = validate_flow(ir)
        warns = [d for d in diags if d.rule_id == "LEDGER_002"]
        assert len(warns) == 1
        assert warns[0].account_id == "acct_x"

    def test_different_accounts_no_warning(self):
        ir = _ir([_step("lt1", "ledger_transaction", ledger_groups=[
            _lg(entries=[
                {"ledger_account_id": "a", "direction": "debit", "amount": 50},
                {"ledger_account_id": "b", "direction": "credit", "amount": 50},
            ]),
        ])])
        diags = validate_flow(ir)
        assert not any(d.rule_id == "LEDGER_002" for d in diags)


class TestNetZeroFlowRule:
    def test_net_zero_triggers_info(self):
        ir = _ir([
            _step("lt1", "ledger_transaction", ledger_groups=[
                _lg(entries=[
                    {"ledger_account_id": "a", "direction": "debit", "amount": 100},
                    {"ledger_account_id": "b", "direction": "credit", "amount": 100},
                ]),
            ]),
            _step("lt2", "ledger_transaction", ledger_groups=[
                _lg(group_id="lg1", entries=[
                    {"ledger_account_id": "b", "direction": "debit", "amount": 100},
                    {"ledger_account_id": "a", "direction": "credit", "amount": 100},
                ]),
            ]),
        ])
        diags = validate_flow(ir)
        infos = [d for d in diags if d.rule_id == "LEDGER_004"]
        assert len(infos) == 1

    def test_non_zero_no_info(self):
        ir = _ir([
            _step("lt1", "ledger_transaction", ledger_groups=[
                _lg(entries=[
                    {"ledger_account_id": "a", "direction": "debit", "amount": 100},
                    {"ledger_account_id": "b", "direction": "credit", "amount": 100},
                ]),
            ]),
            _step("lt2", "ledger_transaction", ledger_groups=[
                _lg(group_id="lg1", entries=[
                    {"ledger_account_id": "a", "direction": "debit", "amount": 50},
                    {"ledger_account_id": "c", "direction": "credit", "amount": 50},
                ]),
            ]),
        ])
        diags = validate_flow(ir)
        infos = [d for d in diags if d.rule_id == "LEDGER_004"]
        assert len(infos) == 0


class TestOrphanedAccountRule:
    def test_account_not_in_actors(self):
        actors = {
            "direct_1": ActorFrame(
                alias="Platform", frame_type="direct", customer_name="Platform",
                slots={"cash": "$ref:ledger_account.cash"},
            ),
        }
        ir = _ir([_step("lt1", "ledger_transaction", ledger_groups=[
            _lg(entries=[
                {"ledger_account_id": "$ref:ledger_account.cash", "direction": "debit", "amount": 100},
                {"ledger_account_id": "$ref:ledger_account.mystery", "direction": "credit", "amount": 100},
            ]),
        ])])
        diags = validate_flow(ir, actor_refs=flatten_actor_refs(actors))
        warns = [d for d in diags if d.rule_id == "LEDGER_005"]
        assert len(warns) == 1
        assert "mystery" in warns[0].account_id

    def test_all_accounts_in_actors(self):
        actors = {
            "direct_1": ActorFrame(
                alias="Platform", frame_type="direct", customer_name="Platform",
                slots={"cash": "$ref:ledger_account.cash", "revenue": "$ref:ledger_account.revenue"},
            ),
        }
        ir = _ir([_step("lt1", "ledger_transaction", ledger_groups=[
            _lg(entries=[
                {"ledger_account_id": "$ref:ledger_account.cash", "direction": "debit", "amount": 100},
                {"ledger_account_id": "$ref:ledger_account.revenue", "direction": "credit", "amount": 100},
            ]),
        ])])
        diags = validate_flow(ir, actor_refs=flatten_actor_refs(actors))
        assert not any(d.rule_id == "LEDGER_005" for d in diags)


class TestEpDeltaRule:
    def test_ep_triggers_info(self):
        ir = _ir([_step("ep1", "expected_payment", payload={"amount": 100})])
        diags = validate_flow(ir)
        infos = [d for d in diags if d.rule_id == "PAYMENT_004"]
        assert len(infos) == 1


class TestFlowValidator:
    def test_custom_rules(self):
        validator = FlowValidator(rules=[])
        ir = _ir([_step("lt1", "ledger_transaction", ledger_groups=[
            _lg(entries=[
                {"ledger_account_id": "a", "direction": "debit", "amount": 100},
                {"ledger_account_id": "b", "direction": "credit", "amount": 50},
            ]),
        ])])
        assert len(validator.validate(ir)) == 0

    def test_default_rules_populated(self):
        assert len(DEFAULT_RULES) >= 7


class TestExampleValidation:
    @pytest.mark.parametrize(
        "json_file",
        sorted(EXAMPLES_DIR.glob("*.json")),
        ids=lambda p: p.stem,
    )
    def test_example_produces_no_errors(self, json_file):
        raw = json.loads(json_file.read_text())
        config = DataLoaderConfig.model_validate(raw)
        for fc in config.funds_flows:
            flow_irs = compile_flows([fc], config)
            for ir in flow_irs:
                diags = validate_flow(ir, actor_refs=flatten_actor_refs(fc.actors))
                errors = [d for d in diags if d.severity == "error"]
                assert not errors, f"{json_file.stem}: {errors}"
