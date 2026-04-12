"""Baseline tests for ``FundsFlowConfig`` + generation DSL (Plan 10a — regression net for 10c)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from models import (
    ActorDatasetOverride,
    ActorFrame,
    FundsFlowConfig,
    FundsFlowStepConfig,
    GenerationRecipeV1,
    OptionalGroupConfig,
)
from models.flow_dsl import RecipeTimingConfig


def _payment_order_step(step_id: str = "po1") -> FundsFlowStepConfig:
    return FundsFlowStepConfig.model_validate(
        {
            "step_id": step_id,
            "type": "payment_order",
            "payment_type": "ach",
            "direction": "credit",
            "amount": 100,
            "originating_account_id": "$ref:internal_account.ia1",
            "receiving_account_id": "$ref:external_account.ea1",
        }
    )


def test_funds_flow_config_minimal():
    fc = FundsFlowConfig(
        ref="test_flow",
        pattern_type="test",
        steps=[_payment_order_step()],
    )
    assert fc.ref == "test_flow"
    assert fc.trace_key == "deal_id"
    assert (
        "{ref}" in fc.trace_metadata[fc.trace_key]
        or "{instance}" in fc.trace_metadata[fc.trace_key]
    )


def test_funds_flow_trace_template_unknown_placeholder():
    with pytest.raises(ValueError, match="unknown placeholders"):
        FundsFlowConfig(
            ref="t",
            pattern_type="p",
            trace_metadata={"deal_id": "{not_a_real_field}"},
            steps=[_payment_order_step()],
        )


def test_funds_flow_extra_forbidden():
    with pytest.raises(ValidationError):
        FundsFlowConfig(
            ref="t",
            pattern_type="p",
            steps=[_payment_order_step()],
            not_a_field=True,  # type: ignore[call-arg]
        )


def test_funds_flow_duplicate_step_id():
    with pytest.raises(ValueError, match="Duplicate step_id"):
        FundsFlowConfig(
            ref="t",
            pattern_type="p",
            steps=[
                _payment_order_step("same"),
                _payment_order_step("same"),
            ],
        )


def test_funds_flow_depends_on_unknown_step():
    bad = FundsFlowStepConfig.model_validate(
        {
            "step_id": "child",
            "type": "payment_order",
            "payment_type": "ach",
            "direction": "credit",
            "amount": 50,
            "originating_account_id": "$ref:internal_account.ia1",
            "receiving_account_id": "$ref:external_account.ea1",
            "depends_on": ["missing_parent"],
        }
    )
    with pytest.raises(ValueError, match="depends_on"):
        FundsFlowConfig(
            ref="t",
            pattern_type="p",
            steps=[_payment_order_step("parent"), bad],
        )


def test_optional_group_duplicate_step_id_with_main_flow():
    """Validator unions main + optional group steps — duplicate ids must fail."""
    dup = _payment_order_step("shared_id")
    og = OptionalGroupConfig.model_validate({"label": "og", "steps": [dup]})
    with pytest.raises(ValueError, match="Duplicate step_id"):
        FundsFlowConfig(
            ref="t",
            pattern_type="p",
            steps=[_payment_order_step("shared_id")],
            optional_groups=[og],
        )


def test_actor_frame_slot_string_promoted():
    af = ActorFrame.model_validate(
        {
            "alias": "buyer",
            "frame_type": "user",
            "slots": {"le": "$ref:legal_entity.buyer_{instance}"},
        }
    )
    assert af.slots["le"].ref == "$ref:legal_entity.buyer_{instance}"


def test_actor_dataset_override_defaults():
    ov = ActorDatasetOverride()
    assert ov.dataset is None


def test_generation_recipe_v1_round_trip():
    r = GenerationRecipeV1(flow_ref="flow_a", instances=3, seed=42)
    dumped = r.model_dump()
    r2 = GenerationRecipeV1.model_validate(dumped)
    assert r2.flow_ref == "flow_a"
    assert r2.instances == 3
    assert r2.seed == 42
    assert r2.version == "v1"


def test_recipe_timing_config_defaults():
    t = RecipeTimingConfig()
    assert t.spread_pattern == "uniform"
    assert t.instance_spread_days == 0


def test_generation_recipe_legacy_staging_promoted():
    r = GenerationRecipeV1.model_validate(
        {"flow_ref": "f", "instances": 5, "seed": 1, "staged_count": 2, "staged_selection": "all"}
    )
    assert any(sr.count == 2 for sr in r.staging_rules)


def test_funds_flow_display_fields_optional():
    fc = FundsFlowConfig(ref="r", pattern_type="p", steps=[_payment_order_step()])
    assert fc.display_title is None
    assert fc.display_summary is None


def test_funds_flow_display_fields_strip_whitespace():
    fc = FundsFlowConfig(
        ref="r",
        pattern_type="p",
        steps=[_payment_order_step()],
        display_title="  Hi  ",
        display_summary="   ",
    )
    assert fc.display_title == "Hi"
    assert fc.display_summary is None


def test_funds_flow_display_title_max_length():
    with pytest.raises(ValidationError):
        FundsFlowConfig(
            ref="r",
            pattern_type="p",
            steps=[_payment_order_step()],
            display_title="x" * 121,
        )


def test_funds_flow_display_summary_max_length():
    with pytest.raises(ValidationError):
        FundsFlowConfig(
            ref="r",
            pattern_type="p",
            steps=[_payment_order_step()],
            display_summary="y" * 501,
        )
