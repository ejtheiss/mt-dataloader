"""Tests for Plan 3 Phase 7: Embedded Resource Patterns.

Covers: ledgerable_type/ledgerable_id on child LTs, reverse_parent
entry flipping, fulfills relationship tracking, and integration with
example JSONs.
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
    _find_reverse_target,
    _flip_entry,
    compile_flows,
    compile_to_plan,
    emit_dataloader_config,
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_minimal_config(**kwargs) -> DataLoaderConfig:
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


def _make_flow_with_inline_lt() -> FundsFlowConfig:
    """PO with inline ledger entries (ledger_inline=True)."""
    return FundsFlowConfig.model_validate({
        "ref": "test_flow",
        "pattern_type": "test",
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
                "step_id": "payout",
                "type": "payment_order",
                "payment_type": "ach",
                "direction": "credit",
                "amount": 10000,
                "originating_account_id": "@actor:direct_1.ops",
                "receiving_account_id": "@actor:direct_1.ops",
                "ledger_inline": True,
                "ledger_entries": [
                    {"ledger_account_id": "@actor:direct_1.cash", "amount": 10000, "direction": "debit"},
                    {"ledger_account_id": "@actor:direct_1.revenue", "amount": 10000, "direction": "credit"},
                ],
            },
        ],
    })


def _make_flow_with_standalone_lt() -> FundsFlowConfig:
    """IPD with standalone ledger entries (ledger_inline=False, the default)."""
    return FundsFlowConfig.model_validate({
        "ref": "test_flow",
        "pattern_type": "test",
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
                "ledger_entries": [
                    {"ledger_account_id": "@actor:direct_1.cash", "amount": 10000, "direction": "debit"},
                    {"ledger_account_id": "@actor:direct_1.revenue", "amount": 10000, "direction": "credit"},
                ],
            },
        ],
    })


def _make_flow_with_reverse_parent() -> FundsFlowConfig:
    """IPD + Return with reverse_parent ledger entries."""
    return FundsFlowConfig.model_validate({
        "ref": "test_flow",
        "pattern_type": "test",
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
                "ledger_entries": [
                    {"ledger_account_id": "@actor:direct_1.cash", "amount": 10000, "direction": "debit"},
                    {"ledger_account_id": "@actor:direct_1.revenue", "amount": 10000, "direction": "credit"},
                ],
            },
            {
                "step_id": "refund",
                "type": "return",
                "depends_on": ["deposit"],
                "returnable_id": "deposit",
                "ledger_entries": "reverse_parent",
            },
        ],
    })


# ---------------------------------------------------------------------------
# _flip_entry unit tests
# ---------------------------------------------------------------------------


class TestFlipEntry:
    def test_debit_becomes_credit(self):
        entry = {"ledger_account_id": "$ref:la.cash", "amount": 100, "direction": "debit"}
        flipped = _flip_entry(entry)
        assert flipped["direction"] == "credit"
        assert flipped["amount"] == 100
        assert flipped["ledger_account_id"] == "$ref:la.cash"

    def test_credit_becomes_debit(self):
        entry = {"ledger_account_id": "$ref:la.rev", "amount": 200, "direction": "credit"}
        flipped = _flip_entry(entry)
        assert flipped["direction"] == "debit"

    def test_original_unchanged(self):
        entry = {"ledger_account_id": "$ref:la.cash", "amount": 100, "direction": "debit"}
        _flip_entry(entry)
        assert entry["direction"] == "debit"


# ---------------------------------------------------------------------------
# _find_reverse_target
# ---------------------------------------------------------------------------


class TestFindReverseTarget:
    def test_return_finds_parent_by_returnable_id(self):
        ipd = IncomingPaymentDetailStep.model_validate({
            "step_id": "deposit", "type": "incoming_payment_detail",
            "payment_type": "ach", "direction": "credit", "amount": 10000,
            "internal_account_id": "$ref:internal_account.ops",
            "ledger_entries": [
                {"ledger_account_id": "$ref:ledger_account.cash", "amount": 10000, "direction": "debit"},
                {"ledger_account_id": "$ref:ledger_account.rev", "amount": 10000, "direction": "credit"},
            ],
        })
        ret = ReturnStep.model_validate({
            "step_id": "refund", "type": "return",
            "depends_on": ["deposit"],
            "returnable_id": "deposit",
            "ledger_entries": "reverse_parent",
        })
        target = _find_reverse_target(ret, [ipd, ret])
        assert target is ipd

    def test_reversal_finds_parent_by_payment_order_id(self):
        po = PaymentOrderStep.model_validate({
            "step_id": "pay", "type": "payment_order",
            "payment_type": "ach", "direction": "credit", "amount": 5000,
            "originating_account_id": "$ref:internal_account.ops",
            "receiving_account_id": "$ref:external_account.cust",
            "ledger_entries": [
                {"ledger_account_id": "$ref:ledger_account.cash", "amount": 5000, "direction": "debit"},
                {"ledger_account_id": "$ref:ledger_account.rev", "amount": 5000, "direction": "credit"},
            ],
        })
        rev = ReversalStep.model_validate({
            "step_id": "rev", "type": "reversal",
            "depends_on": ["pay"],
            "payment_order_id": "pay",
            "reason": "duplicate",
            "ledger_entries": "reverse_parent",
        })
        target = _find_reverse_target(rev, [po, rev])
        assert target is po

    def test_fallback_to_depends_on(self):
        ipd = IncomingPaymentDetailStep.model_validate({
            "step_id": "deposit", "type": "incoming_payment_detail",
            "payment_type": "ach", "direction": "credit", "amount": 10000,
            "internal_account_id": "$ref:internal_account.ops",
            "ledger_entries": [
                {"ledger_account_id": "$ref:ledger_account.cash", "amount": 10000, "direction": "debit"},
                {"ledger_account_id": "$ref:ledger_account.rev", "amount": 10000, "direction": "credit"},
            ],
        })
        ret = ReturnStep.model_validate({
            "step_id": "refund", "type": "return",
            "depends_on": ["deposit"],
            "ledger_entries": "reverse_parent",
        })
        target = _find_reverse_target(ret, [ipd, ret])
        assert target is ipd

    def test_no_parent_returns_none(self):
        ret = ReturnStep.model_validate({
            "step_id": "refund", "type": "return",
            "ledger_entries": "reverse_parent",
        })
        assert _find_reverse_target(ret, [ret]) is None


# ---------------------------------------------------------------------------
# reverse_parent in compile_flows
# ---------------------------------------------------------------------------


class TestReverseParentCompile:
    def test_reverse_parent_flips_entries(self):
        flow = _make_flow_with_reverse_parent()
        config = _make_minimal_config(funds_flows=[flow.model_dump()])
        flow_irs = compile_flows([flow], config)
        ir = flow_irs[0]
        refund_step = next(s for s in ir.steps if s.step_id == "refund")
        assert len(refund_step.ledger_groups) == 1
        entries = refund_step.ledger_groups[0].entries
        assert len(entries) == 2
        debit_entry = next(e for e in entries if e["direction"] == "debit")
        credit_entry = next(e for e in entries if e["direction"] == "credit")
        assert credit_entry["ledger_account_id"] == "$ref:ledger_account.cash"
        assert debit_entry["ledger_account_id"] == "$ref:ledger_account.revenue"

    def test_reverse_parent_preserves_amounts(self):
        flow = _make_flow_with_reverse_parent()
        config = _make_minimal_config(funds_flows=[flow.model_dump()])
        flow_irs = compile_flows([flow], config)
        refund_step = next(s for s in flow_irs[0].steps if s.step_id == "refund")
        amounts = [e["amount"] for e in refund_step.ledger_groups[0].entries]
        assert all(a == 10000 for a in amounts)

    def test_reverse_parent_emits_valid_config(self):
        flow = _make_flow_with_reverse_parent()
        config = _make_minimal_config(funds_flows=[flow.model_dump()])
        flow_irs = compile_flows([flow], config)
        compiled = emit_dataloader_config(flow_irs, base_config=config)
        revalidated = DataLoaderConfig.model_validate(compiled.model_dump())
        assert isinstance(revalidated, DataLoaderConfig)


# ---------------------------------------------------------------------------
# ledgerable_type / ledgerable_id on child LTs
# ---------------------------------------------------------------------------


class TestLedgerableFields:
    def test_standalone_child_lt_has_ledgerable_fields(self):
        flow = _make_flow_with_standalone_lt()
        config = _make_minimal_config(funds_flows=[flow.model_dump()])
        flow_irs = compile_flows([flow], config)
        compiled = emit_dataloader_config(flow_irs, base_config=config)
        child_lts = [
            lt for lt in compiled.ledger_transactions
            if lt.ledgerable_type is not None
        ]
        assert len(child_lts) == 1
        lt = child_lts[0]
        assert lt.ledgerable_type == "incoming_payment_detail"
        assert lt.ledgerable_id.startswith("$ref:incoming_payment_detail.")

    def test_inline_lt_no_ledgerable_fields(self):
        """Inline LTs don't get ledgerable fields (MT sets them automatically)."""
        flow = _make_flow_with_inline_lt()
        config = _make_minimal_config(funds_flows=[flow.model_dump()])
        flow_irs = compile_flows([flow], config)
        compiled = emit_dataloader_config(flow_irs, base_config=config)
        child_lts = [
            lt for lt in compiled.ledger_transactions
            if lt.ledgerable_type is not None
        ]
        assert len(child_lts) == 0

    def test_ledgerable_id_matches_parent_ref(self):
        flow = _make_flow_with_standalone_lt()
        config = _make_minimal_config(funds_flows=[flow.model_dump()])
        flow_irs = compile_flows([flow], config)
        compiled = emit_dataloader_config(flow_irs, base_config=config)
        ipd = compiled.incoming_payment_details[0]
        child_lt = next(
            lt for lt in compiled.ledger_transactions
            if lt.ledgerable_type is not None
        )
        assert child_lt.ledgerable_id == f"$ref:incoming_payment_detail.{ipd.ref}"


# ---------------------------------------------------------------------------
# Integration with example JSONs
# ---------------------------------------------------------------------------


class TestExampleEmbeddedResources:
    def test_stablecoin_ramp_inline_lts_have_no_ledgerable(self):
        """Stablecoin ramp uses inline LTs — no ledgerable fields emitted."""
        raw = json.loads((EXAMPLES_DIR / "stablecoin_ramp.json").read_text())
        config = DataLoaderConfig.model_validate(raw)
        compiled, flow_irs = _compile(config)
        if flow_irs:
            child_lts = [
                lt for lt in compiled.ledger_transactions
                if lt.ledgerable_type is not None
            ]
            assert len(child_lts) == 0

    def test_funds_flow_demo_standalone_lt_has_ledgerable(self):
        """funds_flow_demo has standalone LTs — should get ledgerable fields."""
        raw = json.loads((EXAMPLES_DIR / "funds_flow_demo.json").read_text())
        config = DataLoaderConfig.model_validate(raw)
        compiled, flow_irs = _compile(config)
        if flow_irs:
            child_lts = [
                lt for lt in compiled.ledger_transactions
                if lt.ledgerable_type is not None
            ]
            for lt in child_lts:
                assert lt.ledgerable_type in (
                    "incoming_payment_detail", "payment_order",
                    "expected_payment",
                )
                assert lt.ledgerable_id.startswith("$ref:")

    @pytest.mark.parametrize("filename", sorted(
        p.name for p in EXAMPLES_DIR.glob("*.json")
    ))
    def test_all_examples_compile_with_embedded_changes(self, filename):
        raw = json.loads((EXAMPLES_DIR / filename).read_text())
        config = DataLoaderConfig.model_validate(raw)
        compiled, _ = _compile(config)
        revalidated = DataLoaderConfig.model_validate(compiled.model_dump())
        assert isinstance(revalidated, DataLoaderConfig)
