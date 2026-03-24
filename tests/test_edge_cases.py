"""Tests for Step 11: Edge Cases + Applicability.

Covers StepMatch, ApplicabilityRule, position/insert_after on
OptionalGroupConfig, weighted exclusion-group selection, per-group
overrides via EdgeCaseOverride, FlowIR compilation of OG steps,
and Mermaid opt/alt block rendering.
"""

from __future__ import annotations

import pytest

from flow_compiler import (
    AuthoringConfig,
    FlowIR,
    activate_optional_groups,
    compile_flows,
    compile_to_plan,
    flatten_optional_groups,
    preselect_edge_cases,
    render_mermaid,
)
from flow_compiler.generation import (
    _is_applicable,
    _step_matches,
)
from models import (
    ApplicabilityRule,
    DataLoaderConfig,
    EdgeCaseOverride,
    FundsFlowConfig,
    GenerationRecipeV1,
    OptionalGroupConfig,
    StepMatch,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _base_flow_dict(**overrides) -> dict:
    """Minimal flow dict with an ACH debit PO and an IPD step."""
    d = {
        "ref": "test_flow",
        "pattern_type": "simple",
        "steps": [
            {
                "step_id": "ach_pull",
                "type": "payment_order",
                "payment_type": "ach",
                "direction": "debit",
                "amount": 10000,
                "originating_account_id": "$ref:internal_account.main",
                "receiving_account_id": "$ref:external_account.buyer",
            },
            {
                "step_id": "settle",
                "type": "incoming_payment_detail",
                "payment_type": "ach",
                "amount": 10000,
                "originating_account_id": "$ref:external_account.buyer",
                "internal_account_id": "$ref:internal_account.main",
                "depends_on": ["ach_pull"],
            },
        ],
        "optional_groups": [],
    }
    d.update(overrides)
    return d


def _og(label, steps, **kw) -> dict:
    """Shorthand for an optional group dict."""
    return {"label": label, "steps": steps, **kw}


def _step(step_id, stype="payment_order", **kw) -> dict:
    return {"step_id": step_id, "type": stype, **kw}


def _base_config() -> DataLoaderConfig:
    return DataLoaderConfig(
        connections=[{"ref": "bank", "entity_id": "example1"}],
        internal_accounts=[{
            "ref": "main",
            "name": "Main",
            "party_name": "Platform",
            "currency": "USD",
            "connection_id": "$ref:connection.bank",
        }],
    )


def _flow_config_with_og(og_groups: list[dict]) -> FundsFlowConfig:
    """Build a FundsFlowConfig from base flow + optional groups."""
    d = _base_flow_dict(optional_groups=og_groups)
    return FundsFlowConfig.model_validate(d)


# ===========================================================================
# StepMatch tests
# ===========================================================================


class TestStepMatch:

    def test_match_payment_type(self):
        step = {"type": "payment_order", "payment_type": "ach", "direction": "debit"}
        assert _step_matches(step, {"payment_type": "ach"}) is True
        assert _step_matches(step, {"payment_type": "wire"}) is False

    def test_match_direction(self):
        step = {"type": "payment_order", "payment_type": "ach", "direction": "debit"}
        assert _step_matches(step, {"direction": "debit"}) is True
        assert _step_matches(step, {"direction": "credit"}) is False

    def test_match_resource_type(self):
        step = {"type": "payment_order", "payment_type": "ach"}
        assert _step_matches(step, {"resource_type": "payment_order"}) is True
        assert _step_matches(step, {"resource_type": "incoming_payment_detail"}) is False

    def test_conjunctive_all_must_match(self):
        step = {"type": "payment_order", "payment_type": "ach", "direction": "debit"}
        assert _step_matches(step, {"payment_type": "ach", "direction": "debit"}) is True
        assert _step_matches(step, {"payment_type": "ach", "direction": "credit"}) is False

    def test_empty_match_matches_everything(self):
        assert _step_matches({"type": "payment_order"}, {}) is True

    def test_pydantic_model(self):
        m = StepMatch(payment_type="ach", direction="debit")
        assert m.payment_type == "ach"
        assert m.direction == "debit"
        assert m.resource_type is None


# ===========================================================================
# ApplicabilityRule + _is_applicable tests
# ===========================================================================


class TestApplicability:

    def test_no_rule_always_applicable(self):
        og = _og("test", [_step("s1")])
        assert _is_applicable(og, [{"type": "payment_order"}]) is True

    def test_requires_step_match_satisfied(self):
        og = _og("test", [_step("s1")], applicable_when={
            "requires_step_match": [{"payment_type": "ach", "direction": "debit"}],
        })
        steps = [{"type": "payment_order", "payment_type": "ach", "direction": "debit"}]
        assert _is_applicable(og, steps) is True

    def test_requires_step_match_not_satisfied(self):
        og = _og("test", [_step("s1")], applicable_when={
            "requires_step_match": [{"payment_type": "wire"}],
        })
        steps = [{"type": "payment_order", "payment_type": "ach", "direction": "debit"}]
        assert _is_applicable(og, steps) is False

    def test_excludes_step_match(self):
        og = _og("test", [_step("s1")], applicable_when={
            "excludes_step_match": [{"payment_type": "rtp"}],
        })
        steps_no_rtp = [{"type": "payment_order", "payment_type": "ach"}]
        assert _is_applicable(og, steps_no_rtp) is True

        steps_rtp = [{"type": "payment_order", "payment_type": "rtp"}]
        assert _is_applicable(og, steps_rtp) is False

    def test_depends_on_step(self):
        og = _og("test", [_step("s1")], applicable_when={
            "depends_on_step": "ach_pull",
        })
        steps_with = [{"step_id": "ach_pull", "type": "payment_order"}]
        assert _is_applicable(og, steps_with) is True

        steps_without = [{"step_id": "wire_in", "type": "payment_order"}]
        assert _is_applicable(og, steps_without) is False

    def test_requires_and_excludes_combined(self):
        og = _og("test", [_step("s1")], applicable_when={
            "requires_step_match": [{"payment_type": "ach", "direction": "debit"}],
            "excludes_step_match": [{"payment_type": "rtp"}],
        })
        steps_ach = [
            {"type": "payment_order", "payment_type": "ach", "direction": "debit"},
        ]
        assert _is_applicable(og, steps_ach) is True

        steps_ach_rtp = [
            {"type": "payment_order", "payment_type": "ach", "direction": "debit"},
            {"type": "payment_order", "payment_type": "rtp", "direction": "credit"},
        ]
        assert _is_applicable(og, steps_ach_rtp) is False

    def test_pydantic_model(self):
        rule = ApplicabilityRule(
            requires_step_match=[StepMatch(payment_type="ach")],
            depends_on_step="settle",
        )
        assert len(rule.requires_step_match) == 1
        assert rule.depends_on_step == "settle"


# ===========================================================================
# activate_optional_groups tests
# ===========================================================================


class TestActivateOptionalGroups:

    def test_empty_preselection_activates_nothing(self):
        flow = _base_flow_dict(optional_groups=[
            _og("Return", [_step("ret1", "return", depends_on=["ach_pull"])]),
        ])
        assert activate_optional_groups(flow, set()) == set()

    def test_preselected_labels_activate_when_applicable(self):
        flow = _base_flow_dict(optional_groups=[
            _og("Return", [_step("ret1", "return", depends_on=["ach_pull"])]),
        ])
        assert activate_optional_groups(flow, {"Return"}) == {"Return"}

    def test_applicability_filters_groups(self):
        flow = _base_flow_dict(optional_groups=[
            _og("R01", [_step("ret1", "return", depends_on=["ach_pull"])],
                applicable_when={"requires_step_match": [{"payment_type": "ach"}]}),
            _og("Wire Return", [_step("wret", "return", depends_on=["settle"])],
                applicable_when={"requires_step_match": [{"payment_type": "wire"}]}),
        ])
        activated = activate_optional_groups(flow, {"R01", "Wire Return"})
        assert "R01" in activated
        assert "Wire Return" not in activated

    def test_weighted_exclusion_group(self):
        """When demand exceeds instances, weights apportion the shared pool."""
        flow = _base_flow_dict(optional_groups=[
            _og("A", [_step("sa", "return", depends_on=["ach_pull"])],
                exclusion_group="method", weight=999.0),
            _og("B", [_step("sb", "return", depends_on=["settle"])],
                exclusion_group="method", weight=0.001),
        ])
        wins_a = 0
        for seed in range(100):
            sel = preselect_edge_cases(
                flow, global_count=50, total_instances=20, seed=seed,
            )
            if len(sel["A"]) > len(sel["B"]):
                wins_a += 1
        assert wins_a > 90, f"Heavy weight should usually win pool share: {wins_a}"

    def test_per_group_override_disabled(self):
        flow = _base_flow_dict(optional_groups=[
            _og("Return", [_step("ret1", "return", depends_on=["ach_pull"])]),
        ])
        overrides = {"Return": {"enabled": False}}
        sel = preselect_edge_cases(
            flow, global_count=5, total_instances=10, seed=42, overrides=overrides,
        )
        assert sel["Return"] == set()

    def test_per_group_override_count_zero(self):
        flow = _base_flow_dict(optional_groups=[
            _og("Return", [_step("ret1", "return", depends_on=["ach_pull"])]),
        ])
        overrides = {"Return": {"enabled": True, "count": 0}}
        sel = preselect_edge_cases(
            flow, global_count=5, total_instances=10, seed=42, overrides=overrides,
        )
        assert sel["Return"] == set()


# ===========================================================================
# flatten_optional_groups position tests
# ===========================================================================


class TestFlattenPosition:

    def test_after_no_anchor_appends(self):
        flow = _base_flow_dict(optional_groups=[
            _og("OG", [_step("og1", "return", depends_on=["ach_pull"])], position="after"),
        ])
        flatten_optional_groups(flow)
        ids = [s["step_id"] for s in flow["steps"]]
        assert ids == ["ach_pull", "settle", "og1"]

    def test_after_with_anchor(self):
        flow = _base_flow_dict(optional_groups=[
            _og("OG", [_step("og1", "return", depends_on=["ach_pull"])],
                position="after", insert_after="ach_pull"),
        ])
        flatten_optional_groups(flow)
        ids = [s["step_id"] for s in flow["steps"]]
        assert ids == ["ach_pull", "og1", "settle"]

    def test_before_no_anchor_prepends(self):
        flow = _base_flow_dict(optional_groups=[
            _og("OG", [_step("pre_step", "expected_payment")], position="before"),
        ])
        flatten_optional_groups(flow)
        ids = [s["step_id"] for s in flow["steps"]]
        assert ids[0] == "pre_step"

    def test_before_with_anchor(self):
        flow = _base_flow_dict(optional_groups=[
            _og("OG", [_step("before_settle", "expected_payment")],
                position="before", insert_after="settle"),
        ])
        flatten_optional_groups(flow)
        ids = [s["step_id"] for s in flow["steps"]]
        assert ids.index("before_settle") < ids.index("settle")
        assert ids.index("before_settle") > ids.index("ach_pull")

    def test_replace_removes_anchor_and_rewrites_deps(self):
        flow = {
            "ref": "test",
            "pattern_type": "simple",
            "steps": [
                _step("step_a", "payment_order"),
                _step("step_b", "incoming_payment_detail", depends_on=["step_a"]),
                _step("step_c", "return", depends_on=["step_b"]),
            ],
            "optional_groups": [
                _og("Replace B", [
                    _step("new_b1", "payment_order"),
                    _step("new_b2", "incoming_payment_detail", depends_on=["new_b1"]),
                ], position="replace", insert_after="step_b"),
            ],
        }
        flatten_optional_groups(flow)
        ids = [s["step_id"] for s in flow["steps"]]
        assert "step_b" not in ids
        assert ids == ["step_a", "new_b1", "new_b2", "step_c"]

        step_c = next(s for s in flow["steps"] if s["step_id"] == "step_c")
        assert step_c["depends_on"] == ["new_b2"]

    def test_replace_missing_anchor_falls_back_to_append(self):
        flow = _base_flow_dict(optional_groups=[
            _og("OG", [_step("og1", "return")],
                position="replace", insert_after="nonexistent"),
        ])
        flatten_optional_groups(flow)
        ids = [s["step_id"] for s in flow["steps"]]
        assert ids[-1] == "og1"

    def test_activated_filter_respects_position(self):
        flow = _base_flow_dict(optional_groups=[
            _og("Active", [_step("og1", "return", depends_on=["ach_pull"])],
                position="after", insert_after="ach_pull"),
            _og("Inactive", [_step("og2", "return", depends_on=["settle"])],
                position="before"),
        ])
        flatten_optional_groups(flow, activated_groups={"Active"})
        ids = [s["step_id"] for s in flow["steps"]]
        assert "og1" in ids
        assert "og2" not in ids


# ===========================================================================
# FlowIR compilation of OG steps
# ===========================================================================


class TestFlowIROGSteps:

    def _compile(self, og_groups: list[dict]) -> FlowIR:
        fc = _flow_config_with_og(og_groups)
        base = _base_config()
        irs = compile_flows([fc], base)
        assert len(irs) == 1
        return irs[0]

    def test_og_steps_in_flowir(self):
        ir = self._compile([
            _og("Return", [_step("ret1", "return", depends_on=["ach_pull"])]),
        ])
        step_ids = [s.step_id for s in ir.steps]
        assert "ret1" in step_ids

    def test_og_steps_have_optional_group_label(self):
        ir = self._compile([
            _og("Return", [_step("ret1", "return", depends_on=["ach_pull"])]),
        ])
        ret_step = next(s for s in ir.steps if s.step_id == "ret1")
        assert ret_step.optional_group == "Return"

    def test_og_steps_are_preview_only(self):
        ir = self._compile([
            _og("Return", [_step("ret1", "return", depends_on=["ach_pull"])]),
        ])
        ret_step = next(s for s in ir.steps if s.step_id == "ret1")
        assert ret_step.preview_only is True

    def test_main_steps_not_preview_only(self):
        ir = self._compile([
            _og("Return", [_step("ret1", "return", depends_on=["ach_pull"])]),
        ])
        main_step = next(s for s in ir.steps if s.step_id == "ach_pull")
        assert main_step.preview_only is False
        assert main_step.optional_group is None

    def test_og_steps_not_emitted_as_resources(self):
        fc = _flow_config_with_og([
            _og("Return", [_step("ret1", "return", depends_on=["ach_pull"])]),
        ])
        base = _base_config()
        from flow_compiler.core import emit_dataloader_config
        irs = compile_flows([fc], base)
        result = emit_dataloader_config(irs, base)
        assert len(result.returns) == 0

    def test_position_after_anchor_orders_og_steps_correctly(self):
        ir = self._compile([
            _og("Mid", [_step("mid1", "return", depends_on=["ach_pull"])],
                position="after", insert_after="ach_pull"),
        ])
        step_ids = [s.step_id for s in ir.steps]
        assert step_ids.index("mid1") == step_ids.index("ach_pull") + 1

    def test_position_before_orders_og_steps_first(self):
        ir = self._compile([
            _og("Pre", [_step("pre1", "expected_payment")], position="before"),
        ])
        assert ir.steps[0].step_id == "pre1"

    def test_auto_infer_position_from_depends_on(self):
        """OG steps with depends_on should be placed right after their anchor,
        even without explicit insert_after."""
        ir = self._compile([
            _og("Return", [_step("ret1", "return", depends_on=["ach_pull"])]),
        ])
        step_ids = [s.step_id for s in ir.steps]
        assert step_ids.index("ret1") == step_ids.index("ach_pull") + 1

    def test_auto_infer_uses_latest_depends_on(self):
        """When OG has steps with multiple depends_on, use the latest."""
        ir = self._compile([
            _og("Cleanup", [
                _step("clean1", "return", depends_on=["settle"]),
                _step("clean2", "return", depends_on=["settle"]),
            ]),
        ])
        step_ids = [s.step_id for s in ir.steps]
        settle_idx = step_ids.index("settle")
        clean1_idx = step_ids.index("clean1")
        assert clean1_idx == settle_idx + 1

    def test_auto_infer_no_depends_on_appends_to_end(self):
        """OG steps with no depends_on and no anchor go at the end."""
        ir = self._compile([
            _og("Tail", [_step("tail1", "expected_payment")]),
        ])
        step_ids = [s.step_id for s in ir.steps]
        assert step_ids[-1] == "tail1"


# ===========================================================================
# Mermaid rendering of OG steps
# ===========================================================================


class TestMermaidOGRendering:

    def _render(self, og_groups: list[dict]) -> str:
        fc = _flow_config_with_og(og_groups)
        base = _base_config()
        irs = compile_flows([fc], base)
        return render_mermaid(irs[0], fc)

    def test_opt_block_for_independent_og(self):
        mermaid = self._render([
            _og("Return", [_step("ret1", "return", depends_on=["ach_pull"])]),
        ])
        assert "opt Return" in mermaid

    def test_alt_block_for_exclusion_group(self):
        mermaid = self._render([
            _og("Wire In", [_step("wire_in", "incoming_payment_detail",
                 internal_account_id="$ref:internal_account.main",
                 originating_account_id="$ref:external_account.buyer",
                 payment_type="wire", amount=10000, depends_on=["ach_pull"])],
                exclusion_group="initiation"),
            _og("RTP In", [_step("rtp_in", "incoming_payment_detail",
                 internal_account_id="$ref:internal_account.main",
                 originating_account_id="$ref:external_account.buyer",
                 payment_type="rtp", amount=10000, depends_on=["ach_pull"])],
                exclusion_group="initiation"),
        ])
        assert "alt" in mermaid
        assert "else" in mermaid

    def test_og_steps_appear_in_mermaid_output(self):
        mermaid = self._render([
            _og("Return", [_step("ret1", "return", depends_on=["ach_pull"])]),
        ])
        assert "ret1" in mermaid


# ===========================================================================
# Pydantic model validation
# ===========================================================================


class TestModelValidation:

    def test_optional_group_config_new_fields(self):
        og = OptionalGroupConfig(
            label="Test",
            steps=[{"step_id": "s1", "type": "return"}],
            position="replace",
            insert_after="target",
            applicable_when=ApplicabilityRule(
                requires_step_match=[StepMatch(payment_type="ach")],
            ),
            weight=2.5,
        )
        assert og.position == "replace"
        assert og.insert_after == "target"
        assert og.weight == 2.5
        assert og.applicable_when.requires_step_match[0].payment_type == "ach"

    def test_optional_group_defaults(self):
        og = OptionalGroupConfig(
            label="Test",
            steps=[{"step_id": "s1", "type": "return"}],
        )
        assert og.position == "after"
        assert og.insert_after is None
        assert og.applicable_when is None
        assert og.weight == 1.0

    def test_edge_case_override(self):
        ov = EdgeCaseOverride(enabled=True, count=3)
        assert ov.enabled is True
        assert ov.count == 3

    def test_recipe_with_overrides(self):
        recipe = GenerationRecipeV1(
            flow_ref="test",
            instances=10,
            seed=42,
            edge_case_count=3,
            edge_case_overrides={
                "Return": {"enabled": True, "count": 7},
            },
        )
        assert "Return" in recipe.edge_case_overrides
        assert recipe.edge_case_overrides["Return"].count == 7

    def test_step_match_extra_forbid(self):
        with pytest.raises(Exception):
            StepMatch(payment_type="ach", bogus="field")

    def test_invalid_position_rejected(self):
        with pytest.raises(Exception):
            OptionalGroupConfig(
                label="Test",
                steps=[{"step_id": "s1", "type": "return"}],
                position="invalid",
            )


# ===========================================================================
# View data OG tagging
# ===========================================================================


class TestViewDataOGTagging:

    def test_payment_rows_tagged_with_optional_group(self):
        from flow_views import compute_view_data
        from models import ActorFrame, ActorSlot

        fc = _flow_config_with_og([
            _og("Return", [_step("ret1", "return", depends_on=["ach_pull"])]),
        ])
        fc = fc.model_copy(update={"actors": {
            "platform": ActorFrame(
                alias="Platform",
                slots={"main": ActorSlot(ref="$ref:internal_account.main")},
            ),
            "buyer": ActorFrame(
                alias="Buyer",
                slots={"acct": ActorSlot(ref="$ref:external_account.buyer")},
            ),
        }})
        base = _base_config()
        irs = compile_flows([fc], base)
        vd = compute_view_data(irs[0], fc)

        og_rows = [r for r in vd.payment_rows if r.optional_group]
        main_rows = [r for r in vd.payment_rows if not r.optional_group]
        assert len(og_rows) >= 1
        assert og_rows[0].optional_group == "Return"
        assert all(r.optional_group is None for r in main_rows)
