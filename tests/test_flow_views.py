"""Tests for flow_compiler.flow_views — frozen view types and compute_view_data pass."""

from __future__ import annotations

import dataclasses

import pytest

from flow_compiler import FlowIR, FlowIRStep, LedgerGroup
from flow_compiler.flow_views import (
    FlowViewData,
    LedgerColumnDef,
    LedgerEntryPlacement,
    LedgerViewRow,
    PaymentColumnDef,
    PaymentViewRow,
    _build_ledger_columns,
    _build_payment_columns,
    compute_view_data,
)
from models import (
    FundFlowViewConfig,
    FundsFlowConfig,
    FundsFlowStepConfig,
    LedgerViewConfig,
    PaymentsViewConfig,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _minimal_step(
    step_id: str = "po1",
    rtype: str = "payment_order",
    payload: dict | None = None,
    ledger_groups: tuple[LedgerGroup, ...] = (),
    depends_on: tuple[str, ...] = (),
) -> FlowIRStep:
    return FlowIRStep(
        step_id=step_id,
        flow_ref="test_flow",
        instance_id="0000",
        depends_on=depends_on,
        resource_type=rtype,
        payload=payload or {},
        ledger_groups=ledger_groups,
        trace_metadata={},
    )


def _minimal_ir(steps: list[FlowIRStep] | None = None) -> FlowIR:
    return FlowIR(
        flow_ref="test_flow",
        instance_id="0000",
        pattern_type="test",
        trace_key="deal_id",
        trace_value="TEST-0000",
        trace_metadata={},
        steps=tuple(steps or []),
    )


def _minimal_config(
    actors: dict | None = None,
    view_config: FundFlowViewConfig | None = None,
) -> FundsFlowConfig:
    return FundsFlowConfig(
        ref="test_flow",
        pattern_type="test",
        actors=actors or {},
        steps=[
            FundsFlowStepConfig.model_validate(
                {
                    "step_id": "core",
                    "type": "payment_order",
                    "payment_type": "ach",
                    "direction": "credit",
                    "amount": 100,
                    "originating_account_id": "$ref:internal_account.ia1",
                    "receiving_account_id": "$ref:external_account.ea1",
                }
            ),
        ],
        view_config=view_config,
    )


_PLATFORM_FRAME = {
    "alias": "Platform",
    "frame_type": "direct",
    "customer_name": "Platform",
}


def _direct(slots: dict[str, str]) -> dict:
    return {**_PLATFORM_FRAME, "slots": slots}


def _user(alias: str, slots: dict[str, str]) -> dict:
    return {"alias": alias, "frame_type": "user", "slots": slots}


# ---------------------------------------------------------------------------
# Frozen type immutability
# ---------------------------------------------------------------------------


class TestFrozenTypes:
    def test_ledger_column_def_frozen(self):
        col = LedgerColumnDef(account_ref="$ref:la.x", display_name="X")
        with pytest.raises(dataclasses.FrozenInstanceError):
            col.display_name = "changed"  # type: ignore[misc]

    def test_payment_column_def_frozen(self):
        col = PaymentColumnDef(
            account_ref="$ref:ia.x", display_name="X", account_type="internal_account"
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            col.account_type = "changed"  # type: ignore[misc]

    def test_ledger_entry_placement_frozen(self):
        p = LedgerEntryPlacement(column_ref="$ref:la.x", direction="debit", amount=100)
        with pytest.raises(dataclasses.FrozenInstanceError):
            p.amount = 200  # type: ignore[misc]

    def test_ledger_view_row_frozen(self):
        row = LedgerViewRow(step_ref="test__0000__lt1", description="LT", status="pending")
        with pytest.raises(dataclasses.FrozenInstanceError):
            row.status = "posted"  # type: ignore[misc]

    def test_payment_view_row_frozen(self):
        row = PaymentViewRow(
            step_ref="test__0000__po1",
            description="PO",
            resource_type="payment_order",
            status="pending",
            amount=100,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            row.amount = 200  # type: ignore[misc]

    def test_flow_view_data_frozen(self):
        fvd = FlowViewData()
        with pytest.raises(dataclasses.FrozenInstanceError):
            fvd.available_views = ("ledger",)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Column builders
# ---------------------------------------------------------------------------


class TestBuildLedgerColumns:
    def test_auto_derive_from_actors(self):
        config = _minimal_config(
            actors={
                "direct_1": _direct(
                    {
                        "cash": "$ref:ledger_account.cash",
                        "revenue": "$ref:ledger_account.revenue",
                        "ia": "$ref:internal_account.ia1",
                    }
                ),
            }
        )
        cols = _build_ledger_columns(config)
        refs = [c.account_ref for c in cols]
        assert "$ref:ledger_account.cash" in refs
        assert "$ref:ledger_account.revenue" in refs
        assert "$ref:internal_account.ia1" not in refs

    def test_explicit_refs(self):
        config = _minimal_config(
            actors={
                "direct_1": _direct(
                    {
                        "cash": "$ref:ledger_account.cash",
                        "revenue": "$ref:ledger_account.revenue",
                    }
                ),
            }
        )
        cols = _build_ledger_columns(config, explicit_refs=["$ref:ledger_account.revenue"])
        assert len(cols) == 1
        assert cols[0].account_ref == "$ref:ledger_account.revenue"

    def test_empty_actors(self):
        config = _minimal_config(actors={})
        cols = _build_ledger_columns(config)
        assert cols == ()


class TestBuildPaymentColumns:
    def test_auto_derive_from_actors(self):
        config = _minimal_config(
            actors={
                "direct_1": _direct(
                    {
                        "ia": "$ref:internal_account.ia1",
                        "cash": "$ref:ledger_account.cash",
                    }
                ),
                "user_1": _user("Customer", {"bank": "$ref:external_account.ea1"}),
            }
        )
        cols = _build_payment_columns(config)
        refs = [c.account_ref for c in cols]
        assert "$ref:internal_account.ia1" in refs
        assert "$ref:external_account.ea1" in refs
        assert "$ref:ledger_account.cash" not in refs

    def test_explicit_refs(self):
        config = _minimal_config(
            actors={
                "direct_1": _direct({"ia": "$ref:internal_account.ia1"}),
                "user_1": _user("Customer", {"bank": "$ref:external_account.ea1"}),
            }
        )
        cols = _build_payment_columns(config, explicit_refs=["$ref:internal_account.ia1"])
        assert len(cols) == 1
        assert cols[0].account_ref == "$ref:internal_account.ia1"


# ---------------------------------------------------------------------------
# compute_view_data — basics
# ---------------------------------------------------------------------------


class TestComputeViewData:
    def test_empty_actors_returns_empty(self):
        ir = _minimal_ir()
        config = _minimal_config(actors={})
        result = compute_view_data(ir, config)
        assert result.available_views == ()
        assert result.ledger_rows == ()
        assert result.payment_rows == ()

    def test_ledger_actors_enable_ledger_view(self):
        actors = {
            "direct_1": _direct(
                {
                    "cash": "$ref:ledger_account.cash",
                    "revenue": "$ref:ledger_account.revenue",
                }
            ),
        }
        ir = _minimal_ir(
            [
                _minimal_step(
                    "lt1",
                    "ledger_transaction",
                    payload={
                        "description": "Record cash in",
                    },
                    ledger_groups=(
                        LedgerGroup(
                            group_id="lt1_lg0",
                            inline=False,
                            entries=(
                                {
                                    "ledger_account_id": "$ref:ledger_account.cash",
                                    "direction": "debit",
                                    "amount": 100,
                                },
                                {
                                    "ledger_account_id": "$ref:ledger_account.revenue",
                                    "direction": "credit",
                                    "amount": 100,
                                },
                            ),
                            metadata={},
                        ),
                    ),
                ),
            ]
        )
        config = _minimal_config(actors=actors)
        result = compute_view_data(ir, config)

        assert "ledger" in result.available_views
        assert len(result.ledger_columns) == 2
        assert len(result.ledger_rows) == 1

        row = result.ledger_rows[0]
        assert row.description == "Record cash in"
        assert row.is_standalone is True
        assert len(row.entries) == 2
        debit_entry = [e for e in row.entries if e.direction == "debit"][0]
        assert debit_entry.column_ref == "$ref:ledger_account.cash"
        assert debit_entry.amount == 100

    def test_payment_actors_enable_payments_view(self):
        actors = {
            "direct_1": _direct({"ia": "$ref:internal_account.ia1"}),
            "user_1": _user("Customer", {"bank": "$ref:external_account.ea1"}),
        }
        ir = _minimal_ir(
            [
                _minimal_step(
                    "po1",
                    "payment_order",
                    payload={
                        "description": "Send payment",
                        "direction": "credit",
                        "amount": 500,
                        "originating_account_id": "$ref:internal_account.ia1",
                        "receiving_account_id": "$ref:external_account.ea1",
                    },
                ),
            ]
        )
        config = _minimal_config(actors=actors)
        result = compute_view_data(ir, config)

        assert "payments" in result.available_views
        assert len(result.payment_rows) == 1

        row = result.payment_rows[0]
        assert row.resource_type == "payment_order"
        assert row.amount == 500
        assert row.direction == "credit"
        out_impact = [i for i in row.account_impacts if i.direction == "out"]
        assert len(out_impact) == 1
        assert out_impact[0].column_ref == "$ref:internal_account.ia1"
        assert out_impact[0].fi_role == "ODFI"
        in_impact = [i for i in row.account_impacts if i.direction == "in"]
        assert len(in_impact) == 1
        assert in_impact[0].fi_role == "RDFI"

    def test_both_views_available(self):
        actors = {
            "direct_1": _direct(
                {
                    "ia": "$ref:internal_account.ia1",
                    "cash": "$ref:ledger_account.cash",
                }
            ),
        }
        ir = _minimal_ir(
            [
                _minimal_step(
                    "po1",
                    "payment_order",
                    payload={
                        "direction": "credit",
                        "amount": 100,
                        "originating_account_id": "$ref:internal_account.ia1",
                    },
                ),
                _minimal_step(
                    "lt1",
                    "ledger_transaction",
                    payload={
                        "description": "Record",
                    },
                    ledger_groups=(
                        LedgerGroup(
                            group_id="lt1_lg0",
                            inline=False,
                            entries=(
                                {
                                    "ledger_account_id": "$ref:ledger_account.cash",
                                    "direction": "debit",
                                    "amount": 100,
                                },
                            ),
                            metadata={},
                        ),
                    ),
                ),
            ]
        )
        config = _minimal_config(actors=actors)
        result = compute_view_data(ir, config)

        assert "ledger" in result.available_views
        assert "payments" in result.available_views

    def test_debit_po_reverses_impacts(self):
        actors = {
            "direct_1": _direct({"ia": "$ref:internal_account.ia1"}),
            "user_1": _user("Customer", {"bank": "$ref:external_account.ea1"}),
        }
        ir = _minimal_ir(
            [
                _minimal_step(
                    "po1",
                    "payment_order",
                    payload={
                        "direction": "debit",
                        "amount": 200,
                        "originating_account_id": "$ref:internal_account.ia1",
                        "receiving_account_id": "$ref:external_account.ea1",
                    },
                ),
            ]
        )
        config = _minimal_config(actors=actors)
        result = compute_view_data(ir, config)
        row = result.payment_rows[0]

        ea_impact = [i for i in row.account_impacts if i.column_ref == "$ref:external_account.ea1"]
        assert len(ea_impact) == 1
        assert ea_impact[0].direction == "out"

        ia_impact = [i for i in row.account_impacts if i.column_ref == "$ref:internal_account.ia1"]
        assert len(ia_impact) == 1
        assert ia_impact[0].direction == "in"
        assert ea_impact[0].fi_role == "RDFI"
        assert ia_impact[0].fi_role == "ODFI"


# ---------------------------------------------------------------------------
# View config overrides
# ---------------------------------------------------------------------------


class TestViewConfigOverrides:
    def test_explicit_ledger_columns(self):
        actors = {
            "direct_1": _direct(
                {
                    "cash": "$ref:ledger_account.cash",
                    "revenue": "$ref:ledger_account.revenue",
                    "fees": "$ref:ledger_account.fees",
                }
            ),
        }
        vc = FundFlowViewConfig(
            ledger_view=LedgerViewConfig(
                account_columns=["$ref:ledger_account.cash", "$ref:ledger_account.revenue"],
            ),
        )
        config = _minimal_config(actors=actors, view_config=vc)
        ir = _minimal_ir()
        result = compute_view_data(ir, config)
        assert len(result.ledger_columns) == 2
        assert result.ledger_columns[0].account_ref == "$ref:ledger_account.cash"
        assert result.ledger_columns[1].account_ref == "$ref:ledger_account.revenue"

    def test_explicit_payment_columns(self):
        actors = {
            "direct_1": _direct(
                {
                    "ia1": "$ref:internal_account.ia1",
                    "ia2": "$ref:internal_account.ia2",
                }
            ),
            "user_1": _user("Customer", {"bank": "$ref:external_account.ea1"}),
        }
        vc = FundFlowViewConfig(
            payments_view=PaymentsViewConfig(
                account_columns=["$ref:internal_account.ia1"],
            ),
        )
        config = _minimal_config(actors=actors, view_config=vc)
        ir = _minimal_ir()
        result = compute_view_data(ir, config)
        pay_refs = [c.account_ref for c in result.payment_view_columns]
        assert pay_refs == ["$ref:internal_account.ia1"]


# ---------------------------------------------------------------------------
# Account actor map
# ---------------------------------------------------------------------------


class TestAccountActorMap:
    def test_map_populated(self):
        actors = {
            "direct_1": _direct(
                {
                    "cash": "$ref:ledger_account.cash",
                    "ia": "$ref:internal_account.ia1",
                }
            ),
        }
        config = _minimal_config(actors=actors)
        ir = _minimal_ir()
        result = compute_view_data(ir, config)
        assert result.account_actor_map["$ref:ledger_account.cash"] == "direct_1.cash"
        assert result.account_actor_map["$ref:internal_account.ia1"] == "direct_1.ia"


# ---------------------------------------------------------------------------
# IPD rows
# ---------------------------------------------------------------------------


class TestIPDRows:
    def test_ipd_creates_payment_row(self):
        actors = {
            "direct_1": _direct({"ia": "$ref:internal_account.ia1"}),
        }
        ir = _minimal_ir(
            [
                _minimal_step(
                    "ipd1",
                    "incoming_payment_detail",
                    payload={
                        "description": "ACH deposit",
                        "amount": 300,
                        "internal_account_id": "$ref:internal_account.ia1",
                    },
                ),
            ]
        )
        config = _minimal_config(actors=actors)
        result = compute_view_data(ir, config)
        assert len(result.payment_rows) == 1
        row = result.payment_rows[0]
        assert row.resource_type == "incoming_payment_detail"
        in_impacts = [i for i in row.account_impacts if i.direction == "in"]
        assert len(in_impacts) == 1
        assert in_impacts[0].column_ref == "$ref:internal_account.ia1"
        assert in_impacts[0].fi_role == "RDFI"

    def test_ipd_with_originating_adds_odfi_on_external_column(self):
        actors = {
            "direct_1": _direct({"ia": "$ref:internal_account.ia1"}),
            "user_1": _user("Payer", {"bank": "$ref:external_account.ea1"}),
        }
        ir = _minimal_ir(
            [
                _minimal_step(
                    "ipd1",
                    "incoming_payment_detail",
                    payload={
                        "amount": 300,
                        "internal_account_id": "$ref:internal_account.ia1",
                        "originating_account_id": "$ref:external_account.ea1",
                    },
                ),
            ]
        )
        config = _minimal_config(actors=actors)
        result = compute_view_data(ir, config)
        row = result.payment_rows[0]
        odfi = [i for i in row.account_impacts if i.fi_role == "ODFI"]
        rdfi = [i for i in row.account_impacts if i.fi_role == "RDFI"]
        assert len(odfi) == 1 and odfi[0].direction == "out"
        assert len(rdfi) == 1 and rdfi[0].direction == "in"


# ---------------------------------------------------------------------------
# TLT / standalone LT rows
# ---------------------------------------------------------------------------


class TestTLTExcluded:
    def test_tlt_skipped_in_ledger_view(self):
        actors = {
            "direct_1": _direct({"cash": "$ref:ledger_account.cash"}),
        }
        ir = _minimal_ir(
            [
                _minimal_step(
                    "tlt1",
                    "transition_ledger_transaction",
                    payload={
                        "description": "TLT",
                    },
                    ledger_groups=(
                        LedgerGroup(
                            group_id="tlt1_lg0",
                            inline=False,
                            entries=(
                                {
                                    "ledger_account_id": "$ref:ledger_account.cash",
                                    "direction": "debit",
                                    "amount": 50,
                                },
                            ),
                            metadata={},
                        ),
                    ),
                ),
            ]
        )
        config = _minimal_config(actors=actors)
        result = compute_view_data(ir, config)
        assert len(result.ledger_rows) == 0

    def test_tlt_skipped_in_payments_view(self):
        actors = {
            "direct_1": _direct({"ia": "$ref:internal_account.ia1"}),
        }
        ir = _minimal_ir(
            [
                _minimal_step("tlt1", "transition_ledger_transaction", payload={}),
            ]
        )
        config = _minimal_config(actors=actors)
        result = compute_view_data(ir, config)
        assert len(result.payment_rows) == 0


# ---------------------------------------------------------------------------
# Ledger row for non-LT step (embedded LT)
# ---------------------------------------------------------------------------


class TestChildLTRows:
    def test_payment_row_has_child_lt_when_ledger_groups_exist(self):
        actors = {
            "direct_1": _direct(
                {
                    "ia": "$ref:internal_account.ia1",
                    "cash": "$ref:ledger_account.cash",
                    "revenue": "$ref:ledger_account.revenue",
                }
            ),
        }
        ir = _minimal_ir(
            [
                _minimal_step(
                    "po1",
                    "payment_order",
                    payload={
                        "description": "PO with inline LT",
                        "direction": "credit",
                        "amount": 100,
                        "originating_account_id": "$ref:internal_account.ia1",
                    },
                    ledger_groups=(
                        LedgerGroup(
                            group_id="po1_lg0",
                            inline=True,
                            entries=(
                                {
                                    "ledger_account_id": "$ref:ledger_account.cash",
                                    "direction": "debit",
                                    "amount": 100,
                                },
                                {
                                    "ledger_account_id": "$ref:ledger_account.revenue",
                                    "direction": "credit",
                                    "amount": 100,
                                },
                            ),
                            metadata={},
                        ),
                    ),
                ),
            ]
        )
        config = _minimal_config(actors=actors)
        result = compute_view_data(ir, config)

        assert len(result.payment_rows) == 1
        row = result.payment_rows[0]
        assert row.has_auto_lt is True
        assert len(row.child_lt_rows) == 1
        child = row.child_lt_rows[0]
        assert len(child.entries) == 2
        assert child.ledgerable_type == "payment_order"

    def test_payment_row_no_child_lt_when_no_ledger_groups(self):
        actors = {
            "direct_1": _direct({"ia": "$ref:internal_account.ia1"}),
            "user_1": _user("Customer", {"bank": "$ref:external_account.ea1"}),
        }
        ir = _minimal_ir(
            [
                _minimal_step(
                    "po1",
                    "payment_order",
                    payload={
                        "direction": "credit",
                        "amount": 50,
                        "originating_account_id": "$ref:internal_account.ia1",
                        "receiving_account_id": "$ref:external_account.ea1",
                    },
                ),
            ]
        )
        config = _minimal_config(actors=actors)
        result = compute_view_data(ir, config)
        row = result.payment_rows[0]
        assert row.has_auto_lt is False
        assert row.child_lt_rows == ()


class TestEmbeddedLTRows:
    def test_po_with_inline_lt_produces_ledger_row(self):
        actors = {
            "direct_1": _direct(
                {
                    "ia": "$ref:internal_account.ia1",
                    "cash": "$ref:ledger_account.cash",
                }
            ),
        }
        ir = _minimal_ir(
            [
                _minimal_step(
                    "po1",
                    "payment_order",
                    payload={
                        "description": "PO with inline LT",
                        "direction": "credit",
                        "amount": 100,
                        "originating_account_id": "$ref:internal_account.ia1",
                    },
                    ledger_groups=(
                        LedgerGroup(
                            group_id="po1_lg0",
                            inline=True,
                            entries=(
                                {
                                    "ledger_account_id": "$ref:ledger_account.cash",
                                    "direction": "debit",
                                    "amount": 100,
                                },
                            ),
                            metadata={},
                        ),
                    ),
                ),
            ]
        )
        config = _minimal_config(actors=actors)
        result = compute_view_data(ir, config)

        assert len(result.ledger_rows) == 1
        row = result.ledger_rows[0]
        assert row.is_standalone is False
        assert row.ledgerable_type == "payment_order"
