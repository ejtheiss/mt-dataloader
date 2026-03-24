"""Tests for Step 3.5 — LT Lifecycle & Auto-Ledgering.

Covers: ledger_status, ledger_inline, transition_ledger_transaction,
auto-derive, _inject_lifecycle_depends_on, emitter passthrough,
Mermaid rendering, demo JSON, and passthrough regressions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from models import (
    DataLoaderConfig,
    FundsFlowConfig,
    FundsFlowStepConfig,
    LedgerTransactionStep,
    PaymentOrderStep,
    ReversalStep,
    TransitionLedgerTransactionConfig,
    TransitionLedgerTransactionStep,
)
from flow_compiler import (
    AuthoringConfig,
    FlowIR,
    compile_flows,
    compile_to_plan,
    emit_dataloader_config,
    render_mermaid,
    generate_from_recipe,
)


def _compile(config):
    """Compile a DataLoaderConfig via the pipeline, returning (compiled, flow_irs)."""
    raw = config.model_dump_json().encode()
    plan = compile_to_plan(AuthoringConfig.from_json(raw))
    irs = list(plan.flow_irs) or None
    return plan.config, irs
from models import GenerationRecipeV1

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


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


def _make_flow_dict(**kwargs) -> dict:
    defaults = {
        "ref": "test_flow",
        "pattern_type": "test",
        "trace_key": "deal_id",
        "trace_value_template": "{ref}-{instance}",
        "actors": {
            "direct_1": {
                "alias": "Platform", "frame_type": "direct", "customer_name": "Platform",
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
    return defaults


def _make_flow_config(**kwargs) -> FundsFlowConfig:
    return FundsFlowConfig.model_validate(_make_flow_dict(**kwargs))


def _compile_single_flow(**flow_kwargs) -> FlowIR:
    config = _make_minimal_config()
    flow = _make_flow_config(**flow_kwargs)
    irs = compile_flows([flow], config)
    assert len(irs) == 1
    return irs[0]


def _compile_and_emit(**flow_kwargs) -> DataLoaderConfig:
    config = _make_minimal_config()
    flow = _make_flow_config(**flow_kwargs)
    irs = compile_flows([flow], config)
    return emit_dataloader_config(irs, base_config=config)


# =========================================================================
# ledger_status on FundsFlowStepConfig
# =========================================================================


class TestLedgerStatus:
    def test_pending_validates(self):
        step = FundsFlowStepConfig.model_validate({
            "step_id": "s1", "type": "ledger_transaction",
            "ledger_status": "pending",
            "ledger_entries": [
                {"ledger_account_id": "$ref:ledger_account.a", "amount": 100, "direction": "debit"},
                {"ledger_account_id": "$ref:ledger_account.b", "amount": 100, "direction": "credit"},
            ],
        })
        assert step.ledger_status == "pending"

    def test_posted_validates(self):
        step = FundsFlowStepConfig.model_validate({
            "step_id": "s1", "type": "ledger_transaction",
            "ledger_status": "posted",
            "ledger_entries": [
                {"ledger_account_id": "$ref:ledger_account.a", "amount": 100, "direction": "debit"},
                {"ledger_account_id": "$ref:ledger_account.b", "amount": 100, "direction": "credit"},
            ],
        })
        assert step.ledger_status == "posted"

    def test_none_default(self):
        step = FundsFlowStepConfig.model_validate({
            "step_id": "s1", "type": "ledger_transaction",
            "ledger_entries": [
                {"ledger_account_id": "$ref:ledger_account.a", "amount": 100, "direction": "debit"},
                {"ledger_account_id": "$ref:ledger_account.b", "amount": 100, "direction": "credit"},
            ],
        })
        assert step.ledger_status is None

    def test_archived_rejected(self):
        with pytest.raises(Exception):
            FundsFlowStepConfig.model_validate({
                "step_id": "s1", "type": "ledger_transaction",
                "ledger_status": "archived",
                "ledger_entries": [
                    {"ledger_account_id": "$ref:ledger_account.a", "amount": 100, "direction": "debit"},
                    {"ledger_account_id": "$ref:ledger_account.b", "amount": 100, "direction": "credit"},
                ],
            })

    def test_compiled_standalone_lt_has_status_pending(self):
        emitted = _compile_and_emit(steps=[
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
                "ledger_status": "pending",
                "ledger_entries": [
                    {"ledger_account_id": "@actor:direct_1.cash", "amount": 10000, "direction": "debit"},
                    {"ledger_account_id": "@actor:direct_1.revenue", "amount": 10000, "direction": "credit"},
                ],
            },
        ])
        lts = emitted.ledger_transactions
        assert len(lts) >= 1
        flow_lt = next(lt for lt in lts if "test_flow" in lt.ref)
        assert flow_lt.status == "pending"

    def test_compiled_standalone_lt_no_status_when_none(self):
        emitted = _compile_and_emit()
        lts = emitted.ledger_transactions
        flow_lts = [lt for lt in lts if "test_flow" in lt.ref]
        for lt in flow_lts:
            assert lt.status is None

    def test_direct_lt_step_emits_status(self):
        """Direct LT step (type=ledger_transaction) carries ledger_status."""
        emitted = _compile_and_emit(steps=[
            {
                "step_id": "book",
                "type": "ledger_transaction",
                "ledger_status": "pending",
                "ledger_entries": [
                    {"ledger_account_id": "@actor:direct_1.cash", "amount": 5000, "direction": "debit"},
                    {"ledger_account_id": "@actor:direct_1.revenue", "amount": 5000, "direction": "credit"},
                ],
            },
        ])
        lts = emitted.ledger_transactions
        flow_lt = next(lt for lt in lts if "test_flow" in lt.ref)
        assert flow_lt.status == "pending"

    def test_non_lt_step_companion_lt_gets_status(self):
        """IPD step with ledger_entries + ledger_status emits a companion standalone LT with that status."""
        emitted = _compile_and_emit(steps=[
            {
                "step_id": "deposit",
                "type": "incoming_payment_detail",
                "payment_type": "ach",
                "direction": "credit",
                "amount": 10000,
                "internal_account_id": "@actor:direct_1.ops",
                "ledger_status": "pending",
                "ledger_entries": [
                    {"ledger_account_id": "@actor:direct_1.cash", "amount": 10000, "direction": "debit"},
                    {"ledger_account_id": "@actor:direct_1.revenue", "amount": 10000, "direction": "credit"},
                ],
            },
        ])
        lts = emitted.ledger_transactions
        companion = [lt for lt in lts if "lg0" in lt.ref]
        assert len(companion) == 1
        assert companion[0].status == "pending"


# =========================================================================
# ledger_inline on FundsFlowStepConfig
# =========================================================================


class TestLedgerInline:
    def test_inline_on_po_validates(self):
        step = FundsFlowStepConfig.model_validate({
            "step_id": "po", "type": "payment_order",
            "payment_type": "ach",
            "ledger_inline": True,
            "amount": 10000, "direction": "credit",
            "originating_account_id": "$ref:internal_account.ops",
            "receiving_account_id": "$ref:external_account.vendor",
            "ledger_entries": [
                {"ledger_account_id": "$ref:ledger_account.a", "amount": 10000, "direction": "debit"},
                {"ledger_account_id": "$ref:ledger_account.b", "amount": 10000, "direction": "credit"},
            ],
        })
        assert step.ledger_inline is True

    def test_inline_on_ep_validates(self):
        step = FundsFlowStepConfig.model_validate({
            "step_id": "ep", "type": "expected_payment",
            "ledger_inline": True,
            "internal_account_id": "$ref:internal_account.ops",
            "direction": "credit",
            "ledger_entries": [
                {"ledger_account_id": "$ref:ledger_account.a", "amount": 10000, "direction": "debit"},
                {"ledger_account_id": "$ref:ledger_account.b", "amount": 10000, "direction": "credit"},
            ],
        })
        assert step.ledger_inline is True

    def test_inline_on_reversal_validates(self):
        step = FundsFlowStepConfig.model_validate({
            "step_id": "rev", "type": "reversal",
            "ledger_inline": True,
            "payment_order_id": "$ref:payment_order.po1",
            "reason": "duplicate",
            "ledger_entries": [
                {"ledger_account_id": "$ref:ledger_account.a", "amount": 10000, "direction": "debit"},
                {"ledger_account_id": "$ref:ledger_account.b", "amount": 10000, "direction": "credit"},
            ],
        })
        assert step.ledger_inline is True

    def test_inline_on_lt_type_rejected(self):
        """LedgerTransactionStep doesn't have ledger_inline (structural enforcement)."""
        assert "ledger_inline" not in LedgerTransactionStep.model_fields
        with pytest.raises(Exception, match="Extra inputs"):
            FundsFlowStepConfig.model_validate({
                "step_id": "lt", "type": "ledger_transaction",
                "ledger_inline": True,
                "ledger_entries": [
                    {"ledger_account_id": "$ref:ledger_account.a", "amount": 100, "direction": "debit"},
                    {"ledger_account_id": "$ref:ledger_account.b", "amount": 100, "direction": "credit"},
                ],
            })

    def test_inline_on_transition_rejected(self):
        """TransitionLedgerTransactionStep doesn't have ledger_inline or ledger_entries."""
        assert "ledger_inline" not in TransitionLedgerTransactionStep.model_fields
        with pytest.raises(Exception, match="Extra inputs"):
            FundsFlowStepConfig.model_validate({
                "step_id": "t", "type": "transition_ledger_transaction",
                "ledger_inline": True,
                "status": "posted",
                "ledger_transaction_id": "$ref:ledger_transaction.lt1",
                "ledger_entries": [
                    {"ledger_account_id": "$ref:ledger_account.a", "amount": 100, "direction": "debit"},
                    {"ledger_account_id": "$ref:ledger_account.b", "amount": 100, "direction": "credit"},
                ],
            })

    def test_inline_true_without_entries_is_empty_inline(self):
        """PO with ledger_inline=True but no entries: valid (entries are optional)."""
        step = FundsFlowStepConfig.model_validate({
            "step_id": "po", "type": "payment_order",
            "payment_type": "ach",
            "ledger_inline": True,
            "amount": 10000, "direction": "credit",
            "originating_account_id": "$ref:internal_account.ops",
            "receiving_account_id": "$ref:external_account.vendor",
        })
        assert step.ledger_inline is True
        assert step.ledger_entries is None

    def test_compiled_po_inline_has_ledger_transaction(self):
        config = _make_minimal_config(
            counterparties=[{
                "ref": "vendor",
                "name": "Vendor Co",
                "accounts": [{"sandbox_behavior": "success", "party_name": "V"}],
            }],
            external_accounts=[{
                "ref": "vendor_acct",
                "counterparty_id": "$ref:counterparty.vendor",
                "account_details": [{"account_number": "123456789"}],
                "routing_details": [{"routing_number": "121141822", "routing_number_type": "aba"}],
            }],
        )
        flow = _make_flow_config(
            actors={
                "direct_1": {
                    "alias": "Platform", "frame_type": "direct", "customer_name": "Platform",
                    "slots": {
                        "ops": "$ref:internal_account.ops",
                        "cash": "$ref:ledger_account.cash",
                        "revenue": "$ref:ledger_account.revenue",
                    },
                },
                "direct_2": {
                    "alias": "Vendor", "frame_type": "direct", "customer_name": "Vendor Co",
                    "slots": {"acct": "$ref:external_account.vendor_acct"},
                },
            },
            steps=[
                {
                    "step_id": "payout",
                    "type": "payment_order",
                    "payment_type": "ach",
                    "amount": 5000,
                    "direction": "credit",
                    "originating_account_id": "@actor:direct_1.ops",
                    "receiving_account_id": "@actor:direct_2.acct",
                    "ledger_inline": True,
                    "ledger_status": "pending",
                    "ledger_entries": [
                        {"ledger_account_id": "@actor:direct_1.cash", "amount": 5000, "direction": "debit"},
                        {"ledger_account_id": "@actor:direct_1.revenue", "amount": 5000, "direction": "credit"},
                    ],
                },
            ],
        )
        irs = compile_flows([flow], config)
        emitted = emit_dataloader_config(irs, base_config=config)
        pos = emitted.payment_orders
        flow_pos = [po for po in pos if "test_flow" in po.ref]
        assert len(flow_pos) == 1
        assert flow_pos[0].ledger_transaction is not None
        assert flow_pos[0].ledger_transaction.status == "pending"

    def test_inline_no_standalone_lt(self):
        config = _make_minimal_config(
            counterparties=[{
                "ref": "vendor",
                "name": "Vendor Co",
                "accounts": [{"sandbox_behavior": "success", "party_name": "V"}],
            }],
            external_accounts=[{
                "ref": "vendor_acct",
                "counterparty_id": "$ref:counterparty.vendor",
                "account_details": [{"account_number": "123456789"}],
                "routing_details": [{"routing_number": "121141822", "routing_number_type": "aba"}],
            }],
        )
        flow = _make_flow_config(
            actors={
                "direct_1": {
                    "alias": "Platform", "frame_type": "direct", "customer_name": "Platform",
                    "slots": {
                        "ops": "$ref:internal_account.ops",
                        "cash": "$ref:ledger_account.cash",
                        "revenue": "$ref:ledger_account.revenue",
                    },
                },
                "direct_2": {
                    "alias": "Vendor", "frame_type": "direct", "customer_name": "Vendor Co",
                    "slots": {"acct": "$ref:external_account.vendor_acct"},
                },
            },
            steps=[
                {
                    "step_id": "payout",
                    "type": "payment_order",
                    "payment_type": "ach",
                    "amount": 5000,
                    "direction": "credit",
                    "originating_account_id": "@actor:direct_1.ops",
                    "receiving_account_id": "@actor:direct_2.acct",
                    "ledger_inline": True,
                    "ledger_entries": [
                        {"ledger_account_id": "@actor:direct_1.cash", "amount": 5000, "direction": "debit"},
                        {"ledger_account_id": "@actor:direct_1.revenue", "amount": 5000, "direction": "credit"},
                    ],
                },
            ],
        )
        irs = compile_flows([flow], config)
        emitted = emit_dataloader_config(irs, base_config=config)
        flow_lts = [lt for lt in emitted.ledger_transactions if "test_flow" in lt.ref]
        assert len(flow_lts) == 0

    def test_default_false_emits_standalone(self):
        emitted = _compile_and_emit()
        flow_lts = [lt for lt in emitted.ledger_transactions if "test_flow" in lt.ref]
        assert len(flow_lts) >= 1


# =========================================================================
# TransitionLedgerTransactionConfig
# =========================================================================


class TestTransitionLedgerTransactionConfig:
    def test_valid(self):
        t = TransitionLedgerTransactionConfig.model_validate({
            "ref": "post_lt",
            "ledger_transaction_id": "$ref:ledger_transaction.settle",
            "status": "posted",
        })
        assert t.status == "posted"
        assert t.resource_type == "transition_ledger_transaction"

    def test_archived_valid(self):
        t = TransitionLedgerTransactionConfig.model_validate({
            "ref": "archive_lt",
            "ledger_transaction_id": "$ref:ledger_transaction.settle",
            "status": "archived",
        })
        assert t.status == "archived"

    def test_pending_rejected(self):
        with pytest.raises(Exception):
            TransitionLedgerTransactionConfig.model_validate({
                "ref": "bad",
                "ledger_transaction_id": "$ref:ledger_transaction.settle",
                "status": "pending",
            })

    def test_ledger_transaction_id_required(self):
        with pytest.raises(Exception):
            TransitionLedgerTransactionConfig.model_validate({
                "ref": "bad",
                "status": "posted",
            })

    def test_step_type_validates(self):
        step = FundsFlowStepConfig.model_validate({
            "step_id": "post_lt",
            "type": "transition_ledger_transaction",
            "status": "posted",
            "ledger_transaction_id": "$ref:ledger_transaction.lt1",
            "depends_on": ["settle"],
        })
        assert step.type == "transition_ledger_transaction"


# =========================================================================
# Compiler: transition step compile & emit
# =========================================================================


class TestTransitionCompileEmit:
    def _flow_with_transition(self, **extra_transition_fields):
        defaults = {
            "step_id": "deposit",
            "type": "incoming_payment_detail",
            "payment_type": "ach",
            "direction": "credit",
            "amount": 10000,
            "internal_account_id": "@actor:direct_1.ops",
        }
        settle_step = {
            "step_id": "settle",
            "type": "ledger_transaction",
            "depends_on": ["deposit"],
            "ledger_status": "pending",
            "description": "Book deposit",
            "ledger_entries": [
                {"ledger_account_id": "@actor:direct_1.cash", "amount": 10000, "direction": "debit"},
                {"ledger_account_id": "@actor:direct_1.revenue", "amount": 10000, "direction": "credit"},
            ],
        }
        transition_step = {
            "step_id": "post_settle",
            "type": "transition_ledger_transaction",
            "depends_on": ["settle"],
            "status": "posted",
            **extra_transition_fields,
        }
        return [defaults, settle_step, transition_step]

    def test_transition_emits_to_section(self):
        emitted = _compile_and_emit(steps=self._flow_with_transition())
        assert len(emitted.transition_ledger_transactions) == 1
        t = emitted.transition_ledger_transactions[0]
        assert t.status == "posted"
        assert "test_flow" in t.ref
        assert t.ledger_transaction_id.startswith("$ref:ledger_transaction.")

    def test_transition_depends_on_correct(self):
        emitted = _compile_and_emit(steps=self._flow_with_transition())
        t = emitted.transition_ledger_transactions[0]
        assert len(t.depends_on) >= 1

    def test_auto_derive_from_lt_step(self):
        emitted = _compile_and_emit(steps=self._flow_with_transition())
        t = emitted.transition_ledger_transactions[0]
        assert "$ref:ledger_transaction." in t.ledger_transaction_id

    def test_auto_derive_from_inline_po(self):
        config = _make_minimal_config(
            counterparties=[{
                "ref": "vendor",
                "name": "Vendor Co",
                "accounts": [{"sandbox_behavior": "success", "party_name": "V"}],
            }],
            external_accounts=[{
                "ref": "vendor_acct",
                "counterparty_id": "$ref:counterparty.vendor",
                "account_details": [{"account_number": "123456789"}],
                "routing_details": [{"routing_number": "121141822", "routing_number_type": "aba"}],
            }],
        )
        flow = _make_flow_config(
            actors={
                "direct_1": {
                    "alias": "Platform", "frame_type": "direct", "customer_name": "Platform",
                    "slots": {
                        "ops": "$ref:internal_account.ops",
                        "cash": "$ref:ledger_account.cash",
                        "revenue": "$ref:ledger_account.revenue",
                    },
                },
                "direct_2": {
                    "alias": "Vendor", "frame_type": "direct", "customer_name": "Vendor Co",
                    "slots": {"acct": "$ref:external_account.vendor_acct"},
                },
            },
            steps=[
                {
                    "step_id": "payout",
                    "type": "payment_order",
                    "payment_type": "ach",
                    "amount": 5000,
                    "direction": "credit",
                    "originating_account_id": "@actor:direct_1.ops",
                    "receiving_account_id": "@actor:direct_2.acct",
                    "ledger_inline": True,
                    "ledger_status": "pending",
                    "ledger_entries": [
                        {"ledger_account_id": "@actor:direct_1.cash", "amount": 5000, "direction": "debit"},
                        {"ledger_account_id": "@actor:direct_1.revenue", "amount": 5000, "direction": "credit"},
                    ],
                },
                {
                    "step_id": "post_payout",
                    "type": "transition_ledger_transaction",
                    "depends_on": ["payout"],
                    "status": "posted",
                },
            ],
        )
        irs = compile_flows([flow], config)
        emitted = emit_dataloader_config(irs, base_config=config)
        t = emitted.transition_ledger_transactions[0]
        assert ".ledger_transaction" in t.ledger_transaction_id

    def test_emitted_validates_against_dataloader_config(self):
        emitted = _compile_and_emit(steps=self._flow_with_transition())
        revalidated = DataLoaderConfig.model_validate(emitted.model_dump(exclude_none=True))
        assert len(revalidated.transition_ledger_transactions) == 1

    def test_explicit_ledger_transaction_id_preserved(self):
        steps = self._flow_with_transition(
            ledger_transaction_id="$ref:ledger_transaction.custom_lt"
        )
        flow = _make_flow_config(steps=steps)
        config = _make_minimal_config()
        irs = compile_flows([flow], config)
        emitted = emit_dataloader_config(irs, base_config=config)
        t = emitted.transition_ledger_transactions[0]
        assert "custom_lt" in t.ledger_transaction_id


# =========================================================================
# Mermaid rendering for transition steps
# =========================================================================


class TestMermaidTransition:
    def test_transition_step_rendered(self):
        steps = [
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
                "ledger_status": "pending",
                "ledger_entries": [
                    {"ledger_account_id": "@actor:direct_1.cash", "amount": 10000, "direction": "debit"},
                    {"ledger_account_id": "@actor:direct_1.revenue", "amount": 10000, "direction": "credit"},
                ],
            },
            {
                "step_id": "post_settle",
                "type": "transition_ledger_transaction",
                "depends_on": ["settle"],
                "status": "posted",
                "description": "Post settlement LT",
            },
        ]
        ir = _compile_single_flow(steps=steps)
        flow_config = _make_flow_config(steps=steps)
        mermaid = render_mermaid(ir, flow_config)
        assert "sequenceDiagram" in mermaid
        assert "LT pending" in mermaid
        assert "posted" in mermaid
        assert "Ledger" in mermaid


# =========================================================================
# Integration tests: full lifecycle patterns
# =========================================================================


class TestLifecycleIntegration:
    def test_ipd_pending_lt_post_lifecycle(self):
        """IPD → pending LT → transition to posted."""
        emitted = _compile_and_emit(steps=[
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
                "ledger_status": "pending",
                "ledger_entries": [
                    {"ledger_account_id": "@actor:direct_1.cash", "amount": 10000, "direction": "debit"},
                    {"ledger_account_id": "@actor:direct_1.revenue", "amount": 10000, "direction": "credit"},
                ],
            },
            {
                "step_id": "post_settle",
                "type": "transition_ledger_transaction",
                "depends_on": ["settle"],
                "status": "posted",
            },
        ])
        flow_lts = [lt for lt in emitted.ledger_transactions if "test_flow" in lt.ref]
        assert any(lt.status == "pending" for lt in flow_lts)
        assert len(emitted.transition_ledger_transactions) == 1
        assert emitted.transition_ledger_transactions[0].status == "posted"

    def test_lifecycle_plus_return_and_archive(self):
        """Full lifecycle: IPD → pending LT → post LT → return → archive LT."""
        steps = [
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
                "ledger_status": "pending",
                "ledger_entries": [
                    {"ledger_account_id": "@actor:direct_1.cash", "amount": 10000, "direction": "debit"},
                    {"ledger_account_id": "@actor:direct_1.revenue", "amount": 10000, "direction": "credit"},
                ],
            },
            {
                "step_id": "post_settle",
                "type": "transition_ledger_transaction",
                "depends_on": ["settle"],
                "status": "posted",
            },
            {
                "step_id": "return_deposit",
                "type": "return",
                "depends_on": ["deposit"],
            },
            {
                "step_id": "archive_settle",
                "type": "transition_ledger_transaction",
                "depends_on": ["settle"],
                "status": "archived",
            },
        ]
        emitted = _compile_and_emit(steps=steps)
        transitions = emitted.transition_ledger_transactions
        assert len(transitions) == 2
        statuses = {t.status for t in transitions}
        assert statuses == {"posted", "archived"}
        assert len(emitted.returns) >= 1

    def test_generate_from_recipe_with_transitions(self):
        config = _make_minimal_config(
            funds_flows=[_make_flow_dict(steps=[
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
                    "ledger_status": "pending",
                    "ledger_entries": [
                        {"ledger_account_id": "@actor:direct_1.cash", "amount": 10000, "direction": "debit"},
                        {"ledger_account_id": "@actor:direct_1.revenue", "amount": 10000, "direction": "credit"},
                    ],
                },
                {
                    "step_id": "post_settle",
                    "type": "transition_ledger_transaction",
                    "depends_on": ["settle"],
                    "status": "posted",
                },
            ])],
        )
        recipe = GenerationRecipeV1(
            flow_ref="test_flow", instances=3, seed=42,
        )
        compiled, diagrams, _ = generate_from_recipe(recipe, config)
        assert len(compiled.transition_ledger_transactions) == 3
        assert len(compiled.incoming_payment_details) == 3

    def test_demo_json_compiles_end_to_end(self):
        demo_path = EXAMPLES_DIR / "funds_flow_demo.json"
        with open(demo_path) as f:
            raw = json.load(f)
        config = DataLoaderConfig.model_validate(raw)
        compiled, _ = _compile(config)
        assert len(compiled.incoming_payment_details) >= 1
        assert len(compiled.ledger_transactions) >= 1
        assert len(compiled.transition_ledger_transactions) >= 1


# =========================================================================
# DataLoaderConfig section
# =========================================================================


class TestDataLoaderConfigSection:
    def test_transition_section_default_empty(self):
        config = _make_minimal_config()
        assert config.transition_ledger_transactions == []

    def test_transition_section_populated(self):
        config = _make_minimal_config(
            transition_ledger_transactions=[{
                "ref": "post_lt",
                "ledger_transaction_id": "$ref:ledger_transaction.settle",
                "status": "posted",
            }],
        )
        assert len(config.transition_ledger_transactions) == 1


# =========================================================================
# _inject_lifecycle_depends_on for transitions
# =========================================================================


class TestInjectLifecycleDependsOn:
    def test_transition_gets_depends_on_from_lt_ref(self):
        """The emitter injects a depends_on edge for the transition's target LT."""
        emitted = _compile_and_emit(steps=[
            {
                "step_id": "book",
                "type": "ledger_transaction",
                "ledger_status": "pending",
                "ledger_entries": [
                    {"ledger_account_id": "@actor:direct_1.cash", "amount": 5000, "direction": "debit"},
                    {"ledger_account_id": "@actor:direct_1.revenue", "amount": 5000, "direction": "credit"},
                ],
            },
            {
                "step_id": "post_book",
                "type": "transition_ledger_transaction",
                "depends_on": ["book"],
                "status": "posted",
            },
        ])
        t = emitted.transition_ledger_transactions[0]
        assert len(t.depends_on) >= 1
        assert any("ledger_transaction" in d for d in t.depends_on)


# =========================================================================
# Passthrough regression
# =========================================================================


class TestPassthroughRegression:
    def test_existing_examples_validate(self):
        for json_file in EXAMPLES_DIR.glob("*.json"):
            with open(json_file) as f:
                raw = json.load(f)
            config = DataLoaderConfig.model_validate(raw)
            assert config is not None

    def test_config_without_flows_unchanged(self):
        config = _make_minimal_config()
        result, irs = _compile(config)
        assert irs is None
        assert result.funds_flows == []
        assert result.transition_ledger_transactions == []

    def test_ledger_inline_default_false(self):
        step = FundsFlowStepConfig.model_validate({
            "step_id": "s1", "type": "incoming_payment_detail",
            "payment_type": "ach",
            "direction": "credit",
            "amount": 10000,
            "internal_account_id": "$ref:internal_account.ops",
        })
        assert step.ledger_inline is False

    def test_ledger_status_default_none(self):
        step = FundsFlowStepConfig.model_validate({
            "step_id": "s1", "type": "incoming_payment_detail",
            "payment_type": "ach",
            "direction": "credit",
            "amount": 10000,
            "internal_account_id": "$ref:internal_account.ops",
        })
        assert step.ledger_status is None
