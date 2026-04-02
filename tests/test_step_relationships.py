"""Tests for StepRelationships — the resolved cross-step relationship index.

Covers: lifecycle parent/child (return, reversal, TLT), fulfillment (IPD→EP),
dependency graph, step_by_id lookup, optional-group membership, immutability,
edge cases, and integration smoke with example JSONs.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from flow_compiler import build_step_relationships
from models import (
    DataLoaderConfig,
    ExpectedPaymentStep,
    IncomingPaymentDetailStep,
    InlineLedgerEntryConfig,
    LedgerTransactionStep,
    OptionalGroupConfig,
    PaymentOrderStep,
    ReturnStep,
    ReversalStep,
    TransitionLedgerTransactionStep,
)
from tests.paths import EXAMPLES_DIR

# =========================================================================
# Lifecycle parent — Return → IPD
# =========================================================================


class TestLifecycleParentReturn:
    def test_return_with_explicit_returnable_id(self):
        steps = [
            IncomingPaymentDetailStep(
                step_id="ipd",
                type="incoming_payment_detail",
                payment_type="ach",
                amount=100,
                internal_account_id="ia",
            ),
            ReturnStep(
                step_id="ret",
                type="return",
                depends_on=["ipd"],
                returnable_id="ipd",
            ),
        ]
        rels = build_step_relationships(steps)
        assert rels.lifecycle_parent["ret"] == "ipd"

    def test_return_without_returnable_id_not_in_index(self):
        """Auto-derive is a compilation concern — build_step_relationships
        only records explicit authored references."""
        steps = [
            IncomingPaymentDetailStep(
                step_id="ipd",
                type="incoming_payment_detail",
                payment_type="ach",
                amount=100,
                internal_account_id="ia",
            ),
            ReturnStep(
                step_id="ret",
                type="return",
                depends_on=["ipd"],
            ),
        ]
        rels = build_step_relationships(steps)
        assert "ret" not in rels.lifecycle_parent


# =========================================================================
# Lifecycle parent — Reversal → PO
# =========================================================================


class TestLifecycleParentReversal:
    def test_reversal_with_explicit_payment_order_id(self):
        steps = [
            PaymentOrderStep(
                step_id="po",
                type="payment_order",
                payment_type="ach",
                direction="credit",
                amount=500,
                originating_account_id="ia",
                receiving_account_id="ea",
            ),
            ReversalStep(
                step_id="rev",
                type="reversal",
                depends_on=["po"],
                payment_order_id="po",
            ),
        ]
        rels = build_step_relationships(steps)
        assert rels.lifecycle_parent["rev"] == "po"


# =========================================================================
# Lifecycle parent — TLT → LT
# =========================================================================


class TestLifecycleParentTLT:
    def test_tlt_with_explicit_ledger_transaction_id(self):
        steps = [
            LedgerTransactionStep(
                step_id="lt",
                type="ledger_transaction",
                ledger_entries=[
                    InlineLedgerEntryConfig(
                        amount=100,
                        direction="debit",
                        ledger_account_id="$ref:ledger_account.a",
                    ),
                    InlineLedgerEntryConfig(
                        amount=100,
                        direction="credit",
                        ledger_account_id="$ref:ledger_account.b",
                    ),
                ],
            ),
            TransitionLedgerTransactionStep(
                step_id="tlt",
                type="transition_ledger_transaction",
                depends_on=["lt"],
                ledger_transaction_id="lt",
                status="posted",
            ),
        ]
        rels = build_step_relationships(steps)
        assert rels.lifecycle_parent["tlt"] == "lt"


# =========================================================================
# Lifecycle children — inverse consistency
# =========================================================================


class TestLifecycleChildrenInverse:
    def test_every_parent_has_its_children(self):
        steps = [
            IncomingPaymentDetailStep(
                step_id="ipd",
                type="incoming_payment_detail",
                payment_type="ach",
                amount=100,
                internal_account_id="ia",
            ),
            ReturnStep(
                step_id="ret",
                type="return",
                depends_on=["ipd"],
                returnable_id="ipd",
            ),
            PaymentOrderStep(
                step_id="po",
                type="payment_order",
                payment_type="ach",
                direction="credit",
                amount=500,
                originating_account_id="ia",
                receiving_account_id="ea",
            ),
            ReversalStep(
                step_id="rev",
                type="reversal",
                depends_on=["po"],
                payment_order_id="po",
            ),
        ]
        rels = build_step_relationships(steps)

        for child, parent in rels.lifecycle_parent.items():
            assert parent in rels.lifecycle_children
            assert child in rels.lifecycle_children[parent]

    def test_multiple_children_same_parent(self):
        steps = [
            PaymentOrderStep(
                step_id="po",
                type="payment_order",
                payment_type="ach",
                direction="credit",
                amount=500,
                originating_account_id="ia",
                receiving_account_id="ea",
            ),
            ReversalStep(
                step_id="rev1",
                type="reversal",
                depends_on=["po"],
                payment_order_id="po",
            ),
            ReversalStep(
                step_id="rev2",
                type="reversal",
                depends_on=["po"],
                payment_order_id="po",
            ),
        ]
        rels = build_step_relationships(steps)
        assert len(rels.lifecycle_children["po"]) == 2
        assert set(rels.lifecycle_children["po"]) == {"rev1", "rev2"}


# =========================================================================
# Fulfillment — IPD → EP
# =========================================================================


class TestFulfillment:
    def test_ipd_fulfills_ep(self):
        steps = [
            ExpectedPaymentStep(
                step_id="ep",
                type="expected_payment",
            ),
            IncomingPaymentDetailStep(
                step_id="ipd",
                type="incoming_payment_detail",
                payment_type="ach",
                amount=100,
                internal_account_id="ia",
                fulfills="ep",
            ),
        ]
        rels = build_step_relationships(steps)
        assert rels.fulfills["ipd"] == "ep"
        assert "ipd" in rels.fulfilled_by["ep"]

    def test_fulfills_none_not_recorded(self):
        steps = [
            IncomingPaymentDetailStep(
                step_id="ipd",
                type="incoming_payment_detail",
                payment_type="ach",
                amount=100,
                internal_account_id="ia",
            ),
        ]
        rels = build_step_relationships(steps)
        assert "ipd" not in rels.fulfills


# =========================================================================
# Dependency graph
# =========================================================================


class TestDependencyGraph:
    def test_explicit_depends_on_recorded(self):
        steps = [
            PaymentOrderStep(
                step_id="a",
                type="payment_order",
                payment_type="ach",
                direction="credit",
                amount=100,
                originating_account_id="ia",
                receiving_account_id="ea",
            ),
            IncomingPaymentDetailStep(
                step_id="b",
                type="incoming_payment_detail",
                payment_type="ach",
                amount=100,
                internal_account_id="ia",
                depends_on=["a"],
            ),
            ReturnStep(
                step_id="c",
                type="return",
                depends_on=["b"],
                returnable_id="b",
            ),
        ]
        rels = build_step_relationships(steps)
        assert rels.dependency_graph["a"] == ()
        assert rels.dependency_graph["b"] == ("a",)
        assert rels.dependency_graph["c"] == ("b",)


# =========================================================================
# step_by_id completeness
# =========================================================================


class TestStepById:
    def test_all_steps_present(self):
        steps = [
            PaymentOrderStep(
                step_id="po",
                type="payment_order",
                payment_type="ach",
                direction="credit",
                amount=100,
                originating_account_id="ia",
                receiving_account_id="ea",
            ),
            IncomingPaymentDetailStep(
                step_id="ipd",
                type="incoming_payment_detail",
                payment_type="ach",
                amount=100,
                internal_account_id="ia",
            ),
        ]
        rels = build_step_relationships(steps)
        assert set(rels.step_by_id.keys()) == {"po", "ipd"}
        assert isinstance(rels.step_by_id["po"], PaymentOrderStep)
        assert isinstance(rels.step_by_id["ipd"], IncomingPaymentDetailStep)


# =========================================================================
# Optional group membership
# =========================================================================


class TestOptionalGroup:
    def test_from_optional_groups_arg(self):
        core = PaymentOrderStep(
            step_id="po",
            type="payment_order",
            payment_type="ach",
            direction="credit",
            amount=100,
            originating_account_id="ia",
            receiving_account_id="ea",
        )
        rev = ReversalStep(
            step_id="rev",
            type="reversal",
            depends_on=["po"],
            payment_order_id="po",
        )
        ogs = [
            OptionalGroupConfig(label="reversals", steps=[rev]),
        ]
        rels = build_step_relationships([core, rev], optional_groups=ogs)
        assert rels.optional_group["rev"] == "reversals"
        assert "po" not in rels.optional_group

    def test_from_metadata_stamp(self):
        steps = [
            PaymentOrderStep(
                step_id="po",
                type="payment_order",
                payment_type="ach",
                direction="credit",
                amount=100,
                originating_account_id="ia",
                receiving_account_id="ea",
            ),
            ReversalStep(
                step_id="rev",
                type="reversal",
                depends_on=["po"],
                payment_order_id="po",
                metadata={"_flow_optional_group": "reversals"},
            ),
        ]
        rels = build_step_relationships(steps)
        assert rels.optional_group["rev"] == "reversals"


# =========================================================================
# Edge cases
# =========================================================================


class TestEdgeCases:
    def test_no_lifecycle_steps(self):
        steps = [
            PaymentOrderStep(
                step_id="po",
                type="payment_order",
                payment_type="ach",
                direction="credit",
                amount=100,
                originating_account_id="ia",
                receiving_account_id="ea",
            ),
            ExpectedPaymentStep(
                step_id="ep",
                type="expected_payment",
            ),
        ]
        rels = build_step_relationships(steps)
        assert rels.lifecycle_parent == {}
        assert rels.lifecycle_children == {}

    def test_external_ref_not_in_lifecycle(self):
        """$ref: strings are external refs, not intra-flow step IDs."""
        steps = [
            ReturnStep(
                step_id="ret",
                type="return",
                returnable_id="$ref:incoming_payment_detail.external_thing",
            ),
        ]
        rels = build_step_relationships(steps)
        assert "ret" not in rels.lifecycle_parent

    def test_empty_steps(self):
        rels = build_step_relationships([])
        assert rels.lifecycle_parent == {}
        assert rels.lifecycle_children == {}
        assert rels.fulfills == {}
        assert rels.fulfilled_by == {}
        assert rels.dependency_graph == {}
        assert rels.step_by_id == {}
        assert rels.optional_group == {}


# =========================================================================
# Immutability
# =========================================================================


class TestImmutability:
    def test_relationships_frozen(self):
        steps = [
            IncomingPaymentDetailStep(
                step_id="ipd",
                type="incoming_payment_detail",
                payment_type="ach",
                amount=100,
                internal_account_id="ia",
            ),
            ReturnStep(
                step_id="ret",
                type="return",
                depends_on=["ipd"],
                returnable_id="ipd",
            ),
        ]
        rels = build_step_relationships(steps)
        with pytest.raises(dataclasses.FrozenInstanceError):
            rels.lifecycle_parent = {}  # type: ignore[misc]

    def test_children_are_tuples(self):
        steps = [
            IncomingPaymentDetailStep(
                step_id="ipd",
                type="incoming_payment_detail",
                payment_type="ach",
                amount=100,
                internal_account_id="ia",
            ),
            ReturnStep(
                step_id="ret",
                type="return",
                depends_on=["ipd"],
                returnable_id="ipd",
            ),
        ]
        rels = build_step_relationships(steps)
        assert isinstance(rels.lifecycle_children["ipd"], tuple)
        assert isinstance(rels.dependency_graph["ret"], tuple)


# =========================================================================
# Integration smoke — example JSONs
# =========================================================================


class TestIntegrationSmoke:
    def test_demo_json_builds_relationships(self):
        raw = (EXAMPLES_DIR / "funds_flow_demo.json").read_text()
        config = DataLoaderConfig.model_validate_json(raw)
        for flow in config.funds_flows:
            all_steps = list(flow.steps)
            for og in flow.optional_groups:
                all_steps.extend(og.steps)
            rels = build_step_relationships(
                all_steps,
                optional_groups=flow.optional_groups,
            )
            assert set(rels.step_by_id.keys()) == {s.step_id for s in all_steps}

            # funds_flow_demo has a return_deposit step with depends_on=["deposit"]
            # and no explicit returnable_id — so it won't be in lifecycle_parent
            # (auto-derive hasn't run). But the TLT "post_settle" depends on "settle"
            # and has no explicit ledger_transaction_id either. optional_group tags
            # should be set for the return group steps.
            for og in flow.optional_groups:
                for s in og.steps:
                    assert rels.optional_group.get(s.step_id) == og.label

    def test_stablecoin_ramp_builds_relationships(self):
        raw = (EXAMPLES_DIR / "stablecoin_ramp.json").read_text()
        config = DataLoaderConfig.model_validate_json(raw)
        for flow in config.funds_flows:
            all_steps = list(flow.steps)
            for og in flow.optional_groups:
                all_steps.extend(og.steps)
            rels = build_step_relationships(
                all_steps,
                optional_groups=flow.optional_groups,
            )
            assert set(rels.step_by_id.keys()) == {s.step_id for s in all_steps}
            assert len(rels.dependency_graph) == len(all_steps)

    @pytest.mark.parametrize("example", sorted(EXAMPLES_DIR.glob("*.json")))
    def test_all_examples_build_without_error(self, example: Path):
        raw = example.read_text()
        try:
            config = DataLoaderConfig.model_validate_json(raw)
        except Exception:
            pytest.skip(f"{example.name} failed to parse")
        for flow in config.funds_flows:
            all_steps = list(flow.steps)
            for og in flow.optional_groups:
                all_steps.extend(og.steps)
            rels = build_step_relationships(
                all_steps,
                optional_groups=flow.optional_groups,
            )
            assert len(rels.step_by_id) == len(all_steps)
