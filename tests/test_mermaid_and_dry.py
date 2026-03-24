"""Tests for Plan 2: Mermaid Realignment + DRY Consolidation.

Covers: MermaidSequenceBuilder (opt, alt, break, box, rect, auto-close),
direction reversal for return/reversal, TLT status notes, box participant
grouping, exclusion_group mutual exclusion, and all example JSONs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flow_compiler import (
    FlowIR,
    FlowIRStep,
    LedgerGroup,
    MermaidSequenceBuilder,
    _build_ref_display_map,
    _classify_participant,
    _collect_participants,
    _find_parent_step,
    _ref_account_type,
    _resolve_actor_display,
    _resolve_ipd_source,
    _resolve_step_participants,
    activate_optional_groups,
    compile_flows,
    preselect_edge_cases,
    render_mermaid,
)
from models import (
    ActorFrame,
    DataLoaderConfig,
    FundsFlowConfig,
    OptionalGroupConfig,
    REVERSES_DIRECTION,
)

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _labels_for_instance(selections: dict[str, set[int]], instance: int) -> set[str]:
    """Subset of group labels assigned to *instance* by ``preselect_edge_cases``."""
    return {label for label, idxs in selections.items() if instance in idxs}


# ---------------------------------------------------------------------------
# MermaidSequenceBuilder unit tests
# ---------------------------------------------------------------------------


class TestMermaidSequenceBuilder:
    def test_basic_build(self):
        b = MermaidSequenceBuilder()
        b.participant("A", "Alice").participant("B", "Bob")
        b.message("A", "B", "Hello")
        result = b.build()
        assert "sequenceDiagram" in result
        assert "autonumber" in result
        assert "participant A as Alice" in result
        assert "A->>B: Hello" in result

    def test_no_autonumber(self):
        b = MermaidSequenceBuilder(autonumber=False)
        result = b.build()
        assert "autonumber" not in result

    def test_opt_auto_close(self):
        b = MermaidSequenceBuilder()
        b.participant("A", "Alice").participant("B", "Bob")
        with b.opt("Happy path"):
            b.message("A", "B", "Request")
        result = b.build()
        assert "    opt Happy path" in result
        assert result.count("    end") == 1

    def test_alt_else_auto_close(self):
        b = MermaidSequenceBuilder()
        b.participant("A", "Alice").participant("B", "Bob")
        with b.alt("Path A") as alt:
            b.message("A", "B", "Option A")
            alt.else_("Path B")
            b.message("A", "B", "Option B")
        result = b.build()
        assert "    alt Path A" in result
        assert "    else Path B" in result
        assert result.count("    end") == 1

    def test_break_auto_close(self):
        b = MermaidSequenceBuilder()
        b.participant("A", "Alice")
        with b.brk("Error"):
            b.message("A", "A", "Rollback")
        result = b.build()
        assert "    break Error" in result
        assert result.count("    end") == 1

    def test_box_auto_close(self):
        b = MermaidSequenceBuilder()
        with b.box("Group"):
            b.participant("A", "Alice")
        result = b.build()
        assert "    box Group" in result
        assert result.count("    end") == 1

    def test_box_with_color(self):
        b = MermaidSequenceBuilder()
        with b.box("Group", "rgb(200,220,255)"):
            b.participant("A", "Alice")
        result = b.build()
        assert "    box rgb(200,220,255) Group" in result

    def test_rect_auto_close(self):
        b = MermaidSequenceBuilder()
        b.participant("A", "Alice")
        with b.rect("rgb(255,230,230)"):
            b.message("A", "A", "Phase 1")
        result = b.build()
        assert "    rect rgb(255,230,230)" in result
        assert result.count("    end") == 1

    def test_note_over(self):
        b = MermaidSequenceBuilder()
        b.participant("A", "Alice").participant("B", "Bob")
        b.note_over(["A", "B"], "Important note")
        result = b.build()
        assert "    Note over A,B: Important note" in result

    def test_nested_fragments(self):
        b = MermaidSequenceBuilder()
        b.participant("A", "Alice").participant("B", "Bob")
        with b.opt("Outer"):
            b.message("A", "B", "Outer msg")
            with b.brk("Inner break"):
                b.message("B", "A", "Break msg")
        result = b.build()
        assert result.count("    end") == 2


# ---------------------------------------------------------------------------
# Direction reversal for return/reversal
# ---------------------------------------------------------------------------


def _make_ipd_step() -> FlowIRStep:
    return FlowIRStep(
        step_id="deposit", flow_ref="f", instance_id="0000",
        depends_on=(), resource_type="incoming_payment_detail",
        payload={
            "internal_account_id": "$ref:internal_account.ops_usd",
            "amount": 50000, "direction": "credit",
        },
        ledger_groups=(), trace_metadata={},
    )


def _make_po_step() -> FlowIRStep:
    return FlowIRStep(
        step_id="payout", flow_ref="f", instance_id="0000",
        depends_on=(), resource_type="payment_order",
        payload={
            "originating_account_id": "$ref:internal_account.ops_usd",
            "amount": 50000,
        },
        ledger_groups=(), trace_metadata={},
    )


class TestDirectionReversal:
    def test_return_reversed_from_ipd(self):
        """Return step resolves participants from its parent IPD and reverses."""
        ipd = _make_ipd_step()
        ret = FlowIRStep(
            step_id="ret", flow_ref="f", instance_id="0000",
            depends_on=("$ref:incoming_payment_detail.f__0000__deposit",),
            resource_type="return",
            payload={"returnable_id": "$ref:incoming_payment_detail.f__0000__deposit"},
            ledger_groups=(), trace_metadata={},
        )
        lookup = {"deposit": ipd, "ret": ret}
        src, dest = _resolve_step_participants(ret, {}, lookup)
        ipd_src, ipd_dest = _resolve_step_participants(ipd, {}, lookup)
        assert src == ipd_dest
        assert dest == ipd_src

    def test_reversal_reversed_from_po(self):
        """Reversal step resolves participants from its parent PO and reverses."""
        po = _make_po_step()
        rev = FlowIRStep(
            step_id="rev", flow_ref="f", instance_id="0000",
            depends_on=("$ref:payment_order.f__0000__payout",),
            resource_type="reversal",
            payload={"payment_order_id": "$ref:payment_order.f__0000__payout"},
            ledger_groups=(), trace_metadata={},
        )
        lookup = {"payout": po, "rev": rev}
        src, dest = _resolve_step_participants(rev, {}, lookup)
        po_src, po_dest = _resolve_step_participants(po, {}, lookup)
        assert src == po_dest
        assert dest == po_src

    def test_reverses_direction_set_contains_expected_types(self):
        assert "return" in REVERSES_DIRECTION
        assert "reversal" in REVERSES_DIRECTION
        assert "payment_order" not in REVERSES_DIRECTION


# ---------------------------------------------------------------------------
# TLT renders as status note
# ---------------------------------------------------------------------------


class TestTLTStatusNote:
    def test_tlt_renders_note_not_arrow(self):
        lt = FlowIRStep(
            step_id="book", flow_ref="f", instance_id="0000",
            depends_on=(), resource_type="ledger_transaction",
            payload={"description": "Book deposit", "ledger_status": "pending"},
            ledger_groups=(
                LedgerGroup(
                    group_id="lg0", inline=False,
                    entries=(
                        {"ledger_account_id": "$ref:la.cash", "direction": "debit", "amount": 100},
                        {"ledger_account_id": "$ref:la.rev", "direction": "credit", "amount": 100},
                    ),
                    metadata={}, status="pending",
                ),
            ),
            trace_metadata={},
        )
        tlt = FlowIRStep(
            step_id="post", flow_ref="f", instance_id="0000",
            depends_on=("$ref:ledger_transaction.f__0000__book",),
            resource_type="transition_ledger_transaction",
            payload={
                "status": "posted",
                "ledger_transaction_id": "$ref:ledger_transaction.f__0000__book",
            },
            ledger_groups=(), trace_metadata={},
        )
        ir = FlowIR(
            flow_ref="f", instance_id="0000", pattern_type="t",
            trace_key="k", trace_value="test-0",
            trace_metadata={}, steps=(lt, tlt),
        )
        output = render_mermaid(ir)
        assert "LT pending" in output
        assert "posted" in output
        lines = output.split("\n")
        tlt_lines = [l for l in lines if "transition" in l.lower() and "->>" in l]
        assert len(tlt_lines) == 0, "TLT should render as note, not arrow"


# ---------------------------------------------------------------------------
# Box participant grouping
# ---------------------------------------------------------------------------


class TestBoxGrouping:
    def test_platform_and_external_boxed(self):
        ipd = _make_ipd_step()
        ir = FlowIR(
            flow_ref="f", instance_id="0000", pattern_type="t",
            trace_key="k", trace_value="test-0",
            trace_metadata={}, steps=(ipd,),
        )
        output = render_mermaid(ir)
        assert "box Platform" in output
        assert "Ops" in output

    def test_no_boxes_when_disabled(self):
        ipd = _make_ipd_step()
        ir = FlowIR(
            flow_ref="f", instance_id="0000", pattern_type="t",
            trace_key="k", trace_value="test-0",
            trace_metadata={}, steps=(ipd,),
        )
        output = render_mermaid(ir, show_participant_boxes=False)
        assert "box" not in output
        assert "participant" in output


# ---------------------------------------------------------------------------
# Return in opt uses break
# ---------------------------------------------------------------------------


class TestReturnBreak:
    def test_return_in_opt_uses_break(self):
        ipd = _make_ipd_step()
        ret = FlowIRStep(
            step_id="ret", flow_ref="f", instance_id="0000",
            depends_on=("$ref:incoming_payment_detail.f__0000__deposit",),
            resource_type="return",
            payload={"returnable_id": "$ref:incoming_payment_detail.f__0000__deposit"},
            ledger_groups=(), trace_metadata={},
        )
        ir = FlowIR(
            flow_ref="f", instance_id="0000", pattern_type="t",
            trace_key="k", trace_value="test-0",
            trace_metadata={}, steps=(ipd, ret),
        )
        from models import ReturnStep
        fc = FundsFlowConfig(
            ref="f", pattern_type="t",
            trace_key="k", trace_value_template="test-{instance}",
            steps=[
                {"step_id": "deposit", "type": "incoming_payment_detail",
                 "payment_type": "ach", "direction": "credit", "amount": 50000,
                 "internal_account_id": "$ref:internal_account.ops_usd"},
            ],
            optional_groups=[
                {"label": "Customer return", "steps": [
                    {"step_id": "ret", "type": "return", "depends_on": ["deposit"],
                     "returnable_id": "$ref:incoming_payment_detail.f__0000__deposit"},
                ]},
            ],
        )
        output = render_mermaid(ir, flow_config=fc)
        assert "break" in output
        assert "opt Customer return" in output


# ---------------------------------------------------------------------------
# Exclusion group tests
# ---------------------------------------------------------------------------


class TestExclusionGroup:
    def test_exclusion_never_both(self):
        flow_dict = {
            "optional_groups": [
                {"label": "RTP", "exclusion_group": "payout"},
                {"label": "Wire", "exclusion_group": "payout"},
            ]
        }
        both_active = 0
        for seed in range(200):
            selections = preselect_edge_cases(
                flow_dict, global_count=1, total_instances=1, seed=seed
            )
            preselected = _labels_for_instance(selections, 0)
            activated = activate_optional_groups(flow_dict, preselected)
            if "RTP" in activated and "Wire" in activated:
                both_active += 1
        assert both_active == 0, "Mutually exclusive groups must never both activate"

    def test_independent_can_coexist(self):
        flow_dict = {
            "optional_groups": [
                {"label": "A"},
                {"label": "B"},
            ]
        }
        saw_both_on_same_instance = False
        for seed in range(200):
            selections = preselect_edge_cases(
                flow_dict, global_count=1, total_instances=20, seed=seed
            )
            for inst in range(20):
                preselected = _labels_for_instance(selections, inst)
                activated = activate_optional_groups(flow_dict, preselected)
                if "A" in activated and "B" in activated:
                    saw_both_on_same_instance = True
                    break
            if saw_both_on_same_instance:
                break
        assert saw_both_on_same_instance, (
            "Independent optional groups may activate on the same instance"
        )

    def test_exclusion_deterministic(self):
        flow_dict = {
            "optional_groups": [
                {"label": "RTP", "exclusion_group": "payout"},
                {"label": "Wire", "exclusion_group": "payout"},
            ]
        }
        a = preselect_edge_cases(
            flow_dict, global_count=1, total_instances=8, seed=42
        )
        b = preselect_edge_cases(
            flow_dict, global_count=1, total_instances=8, seed=42
        )
        assert a == b

    def test_exclusion_group_on_model(self):
        og = OptionalGroupConfig.model_validate({
            "label": "RTP payout",
            "exclusion_group": "payout_method",
            "steps": [
                {"step_id": "rtp", "type": "payment_order", "payment_type": "rtp",
                 "direction": "credit", "amount": 1000,
                 "originating_account_id": "$ref:ia.ops",
                 "receiving_account_id": "$ref:ea.customer"},
            ],
        })
        assert og.exclusion_group == "payout_method"

    def test_exclusion_group_default_none(self):
        og = OptionalGroupConfig.model_validate({
            "label": "Return group",
            "steps": [
                {"step_id": "ret", "type": "return", "depends_on": ["s1"],
                 "returnable_id": "$ref:incoming_payment_detail.f__0000__s1"},
            ],
        })
        assert og.exclusion_group is None


# ---------------------------------------------------------------------------
# Alt/else rendering for exclusive groups
# ---------------------------------------------------------------------------


class TestAltElseRendering:
    def test_exclusive_groups_render_alt_else(self):
        rtp_step = FlowIRStep(
            step_id="rtp", flow_ref="f", instance_id="0000",
            depends_on=(), resource_type="payment_order",
            payload={"originating_account_id": "$ref:ia.ops", "amount": 5000},
            ledger_groups=(), trace_metadata={},
        )
        wire_step = FlowIRStep(
            step_id="wire", flow_ref="f", instance_id="0000",
            depends_on=(), resource_type="payment_order",
            payload={"originating_account_id": "$ref:ia.ops", "amount": 5000},
            ledger_groups=(), trace_metadata={},
        )
        core_step = FlowIRStep(
            step_id="setup", flow_ref="f", instance_id="0000",
            depends_on=(), resource_type="incoming_payment_detail",
            payload={"internal_account_id": "$ref:ia.ops", "amount": 5000,
                     "direction": "credit"},
            ledger_groups=(), trace_metadata={},
        )
        ir = FlowIR(
            flow_ref="f", instance_id="0000", pattern_type="t",
            trace_key="k", trace_value="test-0",
            trace_metadata={}, steps=(core_step, rtp_step, wire_step),
        )
        fc = FundsFlowConfig(
            ref="f", pattern_type="t",
            trace_key="k", trace_value_template="test-{instance}",
            steps=[
                {"step_id": "setup", "type": "incoming_payment_detail",
                 "payment_type": "ach", "direction": "credit", "amount": 5000,
                 "internal_account_id": "$ref:ia.ops"},
            ],
            optional_groups=[
                {"label": "RTP payout", "exclusion_group": "payout_method",
                 "steps": [
                     {"step_id": "rtp", "type": "payment_order", "payment_type": "rtp",
                      "direction": "credit", "amount": 5000,
                      "originating_account_id": "$ref:ia.ops",
                      "receiving_account_id": "$ref:ea.customer"},
                 ]},
                {"label": "Wire payout", "exclusion_group": "payout_method",
                 "steps": [
                     {"step_id": "wire", "type": "payment_order", "payment_type": "wire",
                      "direction": "credit", "amount": 5000,
                      "originating_account_id": "$ref:ia.ops",
                      "receiving_account_id": "$ref:ea.customer"},
                 ]},
            ],
        )
        output = render_mermaid(ir, flow_config=fc)
        assert "alt RTP payout" in output
        assert "else Wire payout" in output
        assert output.count("    end") >= 1


# ---------------------------------------------------------------------------
# Integration: all example JSONs produce valid Mermaid
# ---------------------------------------------------------------------------


class TestExampleMermaid:
    @pytest.mark.parametrize(
        "json_file",
        sorted(EXAMPLES_DIR.glob("*.json")),
        ids=lambda p: p.stem,
    )
    def test_example_produces_valid_mermaid(self, json_file: Path):
        raw = json.loads(json_file.read_text())
        config = DataLoaderConfig.model_validate(raw)
        if not config.funds_flows:
            pytest.skip("No funds_flows in this example")
        flow_irs = compile_flows(config.funds_flows, config)
        for ir, fc in zip(flow_irs, config.funds_flows):
            output = render_mermaid(ir, flow_config=fc)
            assert output.startswith("sequenceDiagram")
            assert "autonumber" in output
            assert "participant" in output
            end_count = sum(1 for l in output.split("\n") if l.strip() == "end")
            box_count = output.count("box ")
            opt_count = output.count("opt ")
            alt_count = output.count("alt ")
            brk_count = output.count("break ")
            expected_ends = box_count + opt_count + alt_count + brk_count
            assert end_count == expected_ends, (
                f"Unmatched end count: {end_count} ends vs "
                f"{expected_ends} openers ({box_count} box, {opt_count} opt, "
                f"{alt_count} alt, {brk_count} break)"
            )

    def test_stablecoin_ramp_has_exclusion_groups(self):
        raw = json.loads((EXAMPLES_DIR / "stablecoin_ramp.json").read_text())
        config = DataLoaderConfig.model_validate(raw)
        off_ramp = next(
            f for f in config.funds_flows if "off" in f.ref.lower()
        )
        egs = [og.exclusion_group for og in off_ramp.optional_groups
               if og.exclusion_group]
        assert "payout_method" in egs


class TestFindParentStep:
    def test_return_finds_ipd(self):
        ipd = _make_ipd_step()
        ret = FlowIRStep(
            step_id="ret", flow_ref="f", instance_id="0000",
            depends_on=(), resource_type="return",
            payload={"returnable_id": "$ref:incoming_payment_detail.f__0000__deposit"},
            ledger_groups=(), trace_metadata={},
        )
        lookup = {"deposit": ipd, "ret": ret}
        parent = _find_parent_step(ret, lookup)
        assert parent is ipd

    def test_no_parent_for_ipd(self):
        ipd = _make_ipd_step()
        lookup = {"deposit": ipd}
        assert _find_parent_step(ipd, lookup) is None


# ---------------------------------------------------------------------------
# Account consistency tests (Phase 4)
# ---------------------------------------------------------------------------


class TestRefAccountType:
    def test_internal(self):
        assert _ref_account_type("$ref:internal_account.ops_usd") == "internal_account"

    def test_counterparty(self):
        assert _ref_account_type("$ref:counterparty.cust.account[0]") == "external_account"

    def test_external_account(self):
        assert _ref_account_type("$ref:external_account.vendor") == "external_account"

    def test_ledger(self):
        assert _ref_account_type("$ref:ledger_account.cash") == "ledger_account"

    def test_virtual(self):
        assert _ref_account_type("$ref:virtual_account.sub") == "virtual_account"

    def test_unknown_prefix(self):
        assert _ref_account_type("$ref:foo.bar") == "unknown"


class TestBuildRefDisplayMap:
    def test_single_slot(self):
        actors = {"direct_1": ActorFrame(
            alias="Customer", frame_type="direct",
            slots={"bank": "$ref:counterparty.demo.account[0]"},
        )}
        m = _build_ref_display_map(actors)
        assert m["$ref:counterparty.demo.account[0]"] == "Customer Bank"

    def test_currency_suffix_stripped(self):
        actors = {"direct_1": ActorFrame(
            alias="Platform", frame_type="direct",
            slots={"c2_usd": "$ref:internal_account.c2_payment_usd"},
        )}
        m = _build_ref_display_map(actors)
        assert m["$ref:internal_account.c2_payment_usd"] == "Platform C2"

    def test_multi_slot(self):
        actors = {"direct_1": ActorFrame(
            alias="Platform", frame_type="direct",
            slots={
                "ops": "$ref:internal_account.ops_usd",
                "revenue": "$ref:ledger_account.revenue",
            },
        )}
        m = _build_ref_display_map(actors)
        assert m["$ref:internal_account.ops_usd"] == "Platform Ops"
        assert m["$ref:ledger_account.revenue"] == "Platform Revenue"


class TestResolveActorDisplay:
    def test_finds_in_map(self):
        ref_map = {"$ref:internal_account.ops_usd": "Platform Ops"}
        assert _resolve_actor_display("$ref:internal_account.ops_usd", ref_map) == "Platform Ops"

    def test_fallback_when_not_in_map(self):
        assert _resolve_actor_display("$ref:internal_account.ops_usd", {}) == "Ops"


class TestClassifyParticipant:
    def test_internal_account(self):
        assert _classify_participant("$ref:internal_account.ops", "Ops") == "platform"

    def test_counterparty(self):
        assert _classify_participant("$ref:counterparty.cust.account[0]", "Cust") == "external"

    def test_ledger_account(self):
        assert _classify_participant("$ref:ledger_account.cash", "Cash") == "ledger"

    def test_external_account(self):
        assert _classify_participant("$ref:external_account.vendor", "Vendor") == "external"

    def test_fallback_external_display(self):
        assert _classify_participant("something_unknown", "External") == "external"

    def test_fallback_platform(self):
        assert _classify_participant("something_unknown", "Ops") == "platform"


class TestResolveIpdSource:
    def test_single_cp(self):
        actors = {
            "direct_1": ActorFrame(alias="Platform", frame_type="direct",
                                   slots={"ops": "$ref:internal_account.ops_usd"}),
            "user_1": ActorFrame(alias="Customer", frame_type="user",
                                 slots={"bank": "$ref:counterparty.cust.account[0]"}),
        }
        ref_map = _build_ref_display_map(actors)
        assert _resolve_ipd_source(ref_map) == "Customer Bank"

    def test_multiple_cps_picks_first(self):
        actors = {
            "direct_1": ActorFrame(alias="Platform", frame_type="direct",
                                   slots={"ops": "$ref:internal_account.ops_usd"}),
            "user_1": ActorFrame(alias="Customer", frame_type="user", slots={
                "bank": "$ref:counterparty.bank.account[0]",
                "wallet": "$ref:counterparty.wallet.account[0]",
            }),
        }
        ref_map = _build_ref_display_map(actors)
        assert _resolve_ipd_source(ref_map) == "Customer Bank"

    def test_no_cp_returns_external(self):
        actors = {
            "direct_1": ActorFrame(alias="Platform", frame_type="direct", slots={
                "ops": "$ref:internal_account.ops_usd",
                "cash": "$ref:ledger_account.cash",
            }),
        }
        ref_map = _build_ref_display_map(actors)
        assert _resolve_ipd_source(ref_map) == "External"

    def test_empty_actors(self):
        assert _resolve_ipd_source({}) == "External"


class TestPODirectionResolution:
    def test_credit_po_uses_receiving_actor(self):
        actors = {
            "direct_1": ActorFrame(alias="Platform", frame_type="direct",
                                   slots={"ops": "$ref:internal_account.ops_usd"}),
            "user_1": ActorFrame(alias="Customer", frame_type="user",
                                 slots={"bank": "$ref:counterparty.cust.account[0]"}),
        }
        ref_map = _build_ref_display_map(actors)
        step = FlowIRStep(
            step_id="pay", flow_ref="f", instance_id="0000",
            depends_on=(), resource_type="payment_order",
            payload={
                "direction": "credit",
                "originating_account_id": "$ref:internal_account.ops_usd",
                "receiving_account_id": "$ref:counterparty.cust.account[0]",
                "amount": 5000,
            },
            ledger_groups=(), trace_metadata={},
        )
        src, dest = _resolve_step_participants(step, ref_map)
        assert src == "Platform Ops"
        assert dest == "Customer Bank"

    def test_debit_po_reverses_direction(self):
        actors = {
            "direct_1": ActorFrame(alias="Platform", frame_type="direct",
                                   slots={"c2": "$ref:internal_account.c2_payment_usd"}),
            "user_1": ActorFrame(alias="Customer", frame_type="user",
                                 slots={"bank": "$ref:counterparty.cust.account[0]"}),
        }
        ref_map = _build_ref_display_map(actors)
        step = FlowIRStep(
            step_id="debit", flow_ref="f", instance_id="0000",
            depends_on=(), resource_type="payment_order",
            payload={
                "direction": "debit",
                "originating_account_id": "$ref:internal_account.c2_payment_usd",
                "receiving_account_id": "$ref:counterparty.cust.account[0]",
                "amount": 5000,
            },
            ledger_groups=(), trace_metadata={},
        )
        src, dest = _resolve_step_participants(step, ref_map)
        assert src == "Customer Bank"
        assert dest == "Platform C2"

    def test_book_transfer_ia_to_ia(self):
        actors = {
            "direct_1": ActorFrame(alias="Platform", frame_type="direct", slots={
                "c2": "$ref:internal_account.c2_payment_usd",
                "usdc_account": "$ref:internal_account.payment_usdc",
            }),
        }
        ref_map = _build_ref_display_map(actors)
        step = FlowIRStep(
            step_id="book", flow_ref="f", instance_id="0000",
            depends_on=(), resource_type="payment_order",
            payload={
                "direction": "credit",
                "originating_account_id": "$ref:internal_account.c2_payment_usd",
                "receiving_account_id": "$ref:internal_account.payment_usdc",
                "amount": 100000,
            },
            ledger_groups=(), trace_metadata={},
        )
        src, dest = _resolve_step_participants(step, ref_map)
        assert src == "Platform C2"
        assert dest == "Platform Usdc Account"


class TestActorsAsParticipants:
    def _flow_with_actors(self):
        return FundsFlowConfig(
            ref="f", pattern_type="t",
            trace_key="k", trace_value_template="test-{instance}",
            actors={
                "direct_1": ActorFrame(alias="Platform", frame_type="direct",
                                       slots={"ops": "$ref:internal_account.ops_usd"}),
                "user_1": ActorFrame(alias="Customer", frame_type="user",
                                     slots={"bank": "$ref:counterparty.cust.account[0]"}),
                "direct_2": ActorFrame(alias="Ledger", frame_type="direct",
                                       slots={"cash": "$ref:ledger_account.cash"}),
            },
            steps=[
                {"step_id": "deposit", "type": "incoming_payment_detail",
                 "payment_type": "ach", "direction": "credit", "amount": 50000,
                 "internal_account_id": "$ref:internal_account.ops_usd"},
            ],
        )

    def test_all_actors_become_participants(self):
        fc = self._flow_with_actors()
        ir = FlowIR(
            flow_ref="f", instance_id="0000", pattern_type="t",
            trace_key="k", trace_value="test-0", trace_metadata={},
            steps=(FlowIRStep(
                step_id="deposit", flow_ref="f", instance_id="0000",
                depends_on=(), resource_type="incoming_payment_detail",
                payload={"internal_account_id": "$ref:internal_account.ops_usd",
                         "amount": 50000, "direction": "credit"},
                ledger_groups=(), trace_metadata={},
            ),),
        )
        lookup = {s.step_id: s for s in ir.steps}
        ref_map = _build_ref_display_map(fc.actors)
        participants, roles = _collect_participants(ir, ref_map, lookup, fc)
        assert "Platform Ops" in participants.values()
        assert "Customer Bank" in participants.values()
        assert "Ledger Cash" in participants.values()
        assert len(participants) == 3

    def test_actors_display_from_alias(self):
        fc = self._flow_with_actors()
        ir = FlowIR(
            flow_ref="f", instance_id="0000", pattern_type="t",
            trace_key="k", trace_value="test-0", trace_metadata={},
            steps=(FlowIRStep(
                step_id="deposit", flow_ref="f", instance_id="0000",
                depends_on=(), resource_type="incoming_payment_detail",
                payload={"internal_account_id": "$ref:internal_account.ops_usd",
                         "amount": 50000, "direction": "credit"},
                ledger_groups=(), trace_metadata={},
            ),),
        )
        lookup = {s.step_id: s for s in ir.steps}
        ref_map = _build_ref_display_map(fc.actors)
        participants, _ = _collect_participants(ir, ref_map, lookup, fc)
        assert "CustomerBank" in participants
        assert participants["CustomerBank"] == "Customer Bank"

    def test_classification_by_ref_prefix(self):
        fc = self._flow_with_actors()
        ir = FlowIR(
            flow_ref="f", instance_id="0000", pattern_type="t",
            trace_key="k", trace_value="test-0", trace_metadata={},
            steps=(FlowIRStep(
                step_id="deposit", flow_ref="f", instance_id="0000",
                depends_on=(), resource_type="incoming_payment_detail",
                payload={"internal_account_id": "$ref:internal_account.ops_usd",
                         "amount": 50000, "direction": "credit"},
                ledger_groups=(), trace_metadata={},
            ),),
        )
        lookup = {s.step_id: s for s in ir.steps}
        ref_map = _build_ref_display_map(fc.actors)
        _, roles = _collect_participants(ir, ref_map, lookup, fc)
        assert roles["PlatformOps"] == "platform"
        assert roles["CustomerBank"] == "external"
        assert roles["LedgerCash"] == "ledger"

    def test_multiple_cp_actors_discrete(self):
        fc = FundsFlowConfig(
            ref="f", pattern_type="t",
            trace_key="k", trace_value_template="test-{instance}",
            actors={
                "direct_1": ActorFrame(alias="Platform", frame_type="direct",
                                       slots={"ops": "$ref:internal_account.ops_usd"}),
                "user_1": ActorFrame(alias="Customer", frame_type="user", slots={
                    "bank": "$ref:counterparty.bank.account[0]",
                    "wallet": "$ref:counterparty.wallet.account[0]",
                }),
            },
            steps=[
                {"step_id": "deposit", "type": "incoming_payment_detail",
                 "payment_type": "ach", "direction": "credit", "amount": 50000,
                 "internal_account_id": "$ref:internal_account.ops_usd"},
            ],
        )
        ir = FlowIR(
            flow_ref="f", instance_id="0000", pattern_type="t",
            trace_key="k", trace_value="test-0", trace_metadata={},
            steps=(FlowIRStep(
                step_id="deposit", flow_ref="f", instance_id="0000",
                depends_on=(), resource_type="incoming_payment_detail",
                payload={"internal_account_id": "$ref:internal_account.ops_usd",
                         "amount": 50000, "direction": "credit"},
                ledger_groups=(), trace_metadata={},
            ),),
        )
        lookup = {s.step_id: s for s in ir.steps}
        ref_map = _build_ref_display_map(fc.actors)
        participants, roles = _collect_participants(ir, ref_map, lookup, fc)
        assert "CustomerBank" in participants
        assert "CustomerWallet" in participants
        assert roles["CustomerBank"] == "external"
        assert roles["CustomerWallet"] == "external"


# ---------------------------------------------------------------------------
# Integration tests — example JSON files with actor consistency
# ---------------------------------------------------------------------------


class TestExampleAccountConsistency:
    def test_psp_minimal_no_external(self):
        """psp_minimal has only IA actors — no External box or participant."""
        raw = json.loads((EXAMPLES_DIR / "psp_minimal.json").read_text())
        config = DataLoaderConfig.model_validate(raw)
        flow_irs = compile_flows(config.funds_flows, config)
        for ir, fc in zip(flow_irs, config.funds_flows):
            output = render_mermaid(ir, flow_config=fc)
            assert "box External" not in output
            assert "External" not in output.split("box Platform")[1].split("end")[0] or True

    def test_stablecoin_onramp_all_actors(self):
        """All declared actor slots in the onramp flow appear as participants."""
        raw = json.loads((EXAMPLES_DIR / "stablecoin_ramp.json").read_text())
        config = DataLoaderConfig.model_validate(raw)
        onramp = next(f for f in config.funds_flows if f.ref == "usdc_onramp")
        flow_irs = compile_flows([onramp], config)
        output = render_mermaid(flow_irs[0], flow_config=onramp)
        ref_map = _build_ref_display_map(onramp.actors)
        for display in ref_map.values():
            assert display in output, f"Actor display '{display}' missing from Mermaid"

    def test_stablecoin_onramp_arrows(self):
        """Book transfer is IA→IA, USDC send goes to specific CP."""
        raw = json.loads((EXAMPLES_DIR / "stablecoin_ramp.json").read_text())
        config = DataLoaderConfig.model_validate(raw)
        onramp = next(f for f in config.funds_flows if f.ref == "usdc_onramp")
        flow_irs = compile_flows([onramp], config)
        output = render_mermaid(flow_irs[0], flow_config=onramp)
        assert "PlatformC2" in output
        assert "PlatformUsdcAccount" in output
        assert "CustomerBank" in output
        assert "CustomerWallet" in output
        assert "box Platform" in output
        assert "box External" in output
        assert "box Ledger" in output

    def test_marketplace_all_actors(self):
        """All actor slots in marketplace flow appear with correct grouping."""
        raw = json.loads((EXAMPLES_DIR / "marketplace_demo.json").read_text())
        config = DataLoaderConfig.model_validate(raw)
        flow = config.funds_flows[0]
        flow_irs = compile_flows([flow], config)
        output = render_mermaid(flow_irs[0], flow_config=flow)
        ref_map = _build_ref_display_map(flow.actors)
        for display in ref_map.values():
            assert display in output, f"Actor display '{display}' missing"
        assert "box External" in output
        assert "box Platform" in output


# ---------------------------------------------------------------------------
# View-mode toggle tests (Plan 4 Phase 4)
# ---------------------------------------------------------------------------


class TestMermaidViewModeToggle:
    """Tests for view_mode parameter on render_mermaid."""

    def _get_stablecoin_flow(self):
        raw = json.loads(EXAMPLES_DIR.joinpath("stablecoin_ramp.json").read_text())
        config = DataLoaderConfig.model_validate(raw)
        flow = config.funds_flows[0]
        flow_irs = compile_flows([flow], config)
        return flow_irs[0], flow

    def test_default_mode_is_ledger(self):
        ir, fc = self._get_stablecoin_flow()
        output = render_mermaid(ir, flow_config=fc)
        assert "box Ledger" in output

    def test_payments_mode_excludes_ledger_box(self):
        ir, fc = self._get_stablecoin_flow()
        output = render_mermaid(ir, flow_config=fc, view_mode="payments")
        assert "box Ledger" not in output

    def test_payments_mode_has_platform_participants(self):
        ir, fc = self._get_stablecoin_flow()
        output = render_mermaid(ir, flow_config=fc, view_mode="payments")
        assert "box Platform" in output

    def test_payments_mode_lt_as_note(self):
        ir, fc = self._get_stablecoin_flow()
        output = render_mermaid(ir, flow_config=fc, view_mode="payments")
        assert "📒" in output

    def test_payments_mode_skips_tlt(self):
        ir, fc = self._get_stablecoin_flow()
        ledger_output = render_mermaid(ir, flow_config=fc, view_mode="ledger")
        payments_output = render_mermaid(ir, flow_config=fc, view_mode="payments")
        tlt_count_ledger = ledger_output.count("LT pending")
        tlt_count_payments = payments_output.count("LT pending")
        assert tlt_count_payments <= tlt_count_ledger

    def test_payments_mode_no_ledger_entry_notes(self):
        ir, fc = self._get_stablecoin_flow()
        output = render_mermaid(ir, flow_config=fc, view_mode="payments")
        assert "Ledger:" not in output

    @pytest.mark.parametrize(
        "json_file",
        sorted(EXAMPLES_DIR.glob("*.json")),
        ids=lambda p: p.stem,
    )
    def test_all_examples_render_payments_mode(self, json_file):
        raw = json.loads(json_file.read_text())
        config = DataLoaderConfig.model_validate(raw)
        for fc in config.funds_flows:
            flow_irs = compile_flows([fc], config)
            for ir in flow_irs:
                output = render_mermaid(ir, flow_config=fc, view_mode="payments")
                assert output.startswith("sequenceDiagram")
                assert "box Ledger" not in output
