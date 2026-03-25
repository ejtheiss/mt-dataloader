"""Tests for flow_compiler/timing.py — spread patterns, payment defaults, business days."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from flow_compiler.timing import (
    _advance_business_days,
    _hours_to_days,
    _is_business_day,
    _resolve_step_delay,
    _skip_to_business_day,
    compute_effective_dates,
    compute_spread_offsets,
)
from models.flow_dsl import FlowTimingConfig, RecipeTimingConfig


# ---------------------------------------------------------------------------
# Spread patterns — deterministic
# ---------------------------------------------------------------------------


class TestSpreadOffsets:
    def test_zero_spread_returns_all_zeros(self):
        offsets = compute_spread_offsets(5, 0, "uniform", seed=1)
        assert offsets == [0.0] * 5

    def test_single_instance_uniform(self):
        offsets = compute_spread_offsets(1, 30, "uniform", seed=1)
        assert offsets == [0.0]

    def test_uniform_even_spacing(self):
        offsets = compute_spread_offsets(5, 20, "uniform", seed=1)
        assert len(offsets) == 5
        assert offsets[0] == pytest.approx(0.0)
        assert offsets[-1] == pytest.approx(20.0)
        step = offsets[1] - offsets[0]
        for i in range(1, len(offsets)):
            assert offsets[i] - offsets[i - 1] == pytest.approx(step, abs=1e-9)

    def test_ramp_up_front_loaded(self):
        offsets = compute_spread_offsets(5, 30, "ramp_up", seed=1)
        assert offsets[0] == pytest.approx(0.0)
        assert offsets[-1] == pytest.approx(30.0)
        gaps = [offsets[i + 1] - offsets[i] for i in range(len(offsets) - 1)]
        for i in range(len(gaps) - 1):
            assert gaps[i] <= gaps[i + 1] + 1e-9

    def test_ramp_down_back_loaded(self):
        offsets = compute_spread_offsets(5, 30, "ramp_down", seed=1)
        assert offsets[0] == pytest.approx(0.0)
        assert offsets[-1] == pytest.approx(30.0)
        gaps = [offsets[i + 1] - offsets[i] for i in range(len(offsets) - 1)]
        for i in range(len(gaps) - 1):
            assert gaps[i] >= gaps[i + 1] - 1e-9

    def test_clustered_uses_three_centers(self):
        offsets = compute_spread_offsets(6, 30, "clustered", seed=1)
        assert len(offsets) == 6
        unique = sorted(set(round(o) for o in offsets))
        assert 0 in unique
        assert 15 in unique
        assert 30 in unique

    def test_deterministic_for_same_seed(self):
        a = compute_spread_offsets(10, 30, "uniform", seed=42)
        b = compute_spread_offsets(10, 30, "uniform", seed=42)
        assert a == b

    def test_different_seeds_differ(self):
        a = compute_spread_offsets(10, 30, "uniform", seed=42, jitter_days=2)
        b = compute_spread_offsets(10, 30, "uniform", seed=99, jitter_days=2)
        assert a != b

    def test_jitter_shifts_offsets(self):
        no_jitter = compute_spread_offsets(5, 30, "uniform", seed=42, jitter_days=0)
        with_jitter = compute_spread_offsets(5, 30, "uniform", seed=42, jitter_days=2)
        assert no_jitter != with_jitter
        for o in with_jitter:
            assert o >= 0.0

    def test_zero_instances(self):
        assert compute_spread_offsets(0, 30, "uniform", seed=1) == []


# ---------------------------------------------------------------------------
# Business day helpers
# ---------------------------------------------------------------------------


class TestBusinessDays:
    def test_weekday_is_business(self):
        mon = date(2025, 3, 24)
        assert _is_business_day(mon)

    def test_saturday_not_business(self):
        sat = date(2025, 3, 22)
        assert not _is_business_day(sat)

    def test_sunday_not_business(self):
        sun = date(2025, 3, 23)
        assert not _is_business_day(sun)

    def test_christmas_not_business(self):
        xmas = date(2025, 12, 25)
        assert not _is_business_day(xmas)

    def test_advance_zero_days(self):
        d = date(2025, 3, 24)
        assert _advance_business_days(d, 0) == d

    def test_advance_skips_weekend(self):
        fri = date(2025, 3, 21)
        result = _advance_business_days(fri, 1)
        assert result == date(2025, 3, 24)  # Monday

    def test_advance_two_business_days(self):
        fri = date(2025, 3, 21)
        result = _advance_business_days(fri, 2)
        assert result == date(2025, 3, 25)  # Tuesday

    def test_skip_to_business_day_already_business(self):
        mon = date(2025, 3, 24)
        assert _skip_to_business_day(mon) == mon

    def test_skip_to_business_day_from_saturday(self):
        sat = date(2025, 3, 22)
        assert _skip_to_business_day(sat) == date(2025, 3, 24)


# ---------------------------------------------------------------------------
# Hours → days conversion
# ---------------------------------------------------------------------------


class TestHoursToDays:
    def test_zero(self):
        assert _hours_to_days(0.0) == 0

    def test_24h_is_1_day(self):
        assert _hours_to_days(24.0) == 1

    def test_48h_is_2_days(self):
        assert _hours_to_days(48.0) == 2

    def test_rounding(self):
        assert _hours_to_days(36.0) == 2   # 1.5 rounds to 2
        assert _hours_to_days(11.0) == 0   # 0.46 rounds to 0
        assert _hours_to_days(12.0) == 0   # 0.5 rounds to 0 (banker's rounding)
        assert _hours_to_days(13.0) == 1   # 0.54 rounds to 1

    def test_negative_clamps_to_zero(self):
        assert _hours_to_days(-10.0) == 0


# ---------------------------------------------------------------------------
# Payment-type default delays (now in hours)
# ---------------------------------------------------------------------------


class TestResolveStepDelay:
    def test_ach_payment_order_default(self):
        step = {"step_id": "pay", "type": "payment_order", "payment_type": "ach"}
        delay, jitter, biz = _resolve_step_delay(step, None, None)
        assert delay == 48.0  # 2 days in hours
        assert jitter == 0.0
        assert biz is True

    def test_wire_instant(self):
        step = {"step_id": "pay", "type": "payment_order", "payment_type": "wire"}
        delay, _, _ = _resolve_step_delay(step, None, None)
        assert delay == 0.0

    def test_return_ach_default(self):
        step = {"step_id": "ret", "type": "return", "payment_type": "ach"}
        delay, _, _ = _resolve_step_delay(step, None, None)
        assert delay == 72.0  # 3 days in hours

    def test_step_timing_overrides_default(self):
        step = {
            "step_id": "pay",
            "type": "payment_order",
            "payment_type": "ach",
            "timing": {"delay_hours": 120.0, "delay_jitter_hours": 24.0, "business_days_only": False},
        }
        delay, jitter, biz = _resolve_step_delay(step, None, None)
        assert delay == 120.0
        assert jitter == 24.0
        assert biz is False

    def test_flow_timing_defaults(self):
        step = {"step_id": "pay", "type": "payment_order", "payment_type": "book"}
        flow_timing = FlowTimingConfig(default_delay_hours=72.0, default_jitter_hours=12.0)
        delay, jitter, _ = _resolve_step_delay(step, flow_timing, None)
        assert delay == 72.0
        assert jitter == 12.0

    def test_recipe_override_takes_precedence(self):
        step = {
            "step_id": "pay",
            "type": "payment_order",
            "payment_type": "ach",
            "timing": {"delay_hours": 120.0},
        }
        overrides = {"pay": 240.0}
        delay, _, _ = _resolve_step_delay(step, None, overrides)
        assert delay == 240.0


# ---------------------------------------------------------------------------
# compute_effective_dates — end-to-end
# ---------------------------------------------------------------------------


class TestComputeEffectiveDates:
    def _make_flow_dict(self, steps):
        return {"steps": steps}

    def test_zero_timing_no_change(self):
        """With no timing config, steps get today's date."""
        steps = [
            {"step_id": "a", "type": "payment_order", "payment_type": "book", "depends_on": []},
        ]
        flow = self._make_flow_dict(steps)
        result = compute_effective_dates(flow, seed=1)
        assert result["steps"][0]["effective_date"] == date.today().isoformat()

    def test_existing_effective_date_preserved(self):
        steps = [
            {
                "step_id": "a", "type": "payment_order", "payment_type": "ach",
                "depends_on": [], "effective_date": "2025-06-01",
            },
        ]
        flow = self._make_flow_dict(steps)
        compute_effective_dates(flow, seed=1)
        assert flow["steps"][0]["effective_date"] == "2025-06-01"

    def test_spread_offset_shifts_base(self):
        steps = [
            {"step_id": "a", "type": "payment_order", "payment_type": "book", "depends_on": []},
        ]
        flow = self._make_flow_dict(steps)
        base = date.today() + timedelta(days=10)
        compute_effective_dates(flow, spread_offset_days=10, seed=1)
        computed = date.fromisoformat(flow["steps"][0]["effective_date"])
        assert computed >= base or computed == _skip_to_business_day(base)

    def test_depends_on_chains_dates(self):
        steps = [
            {"step_id": "a", "type": "payment_order", "payment_type": "ach", "depends_on": []},
            {"step_id": "b", "type": "payment_order", "payment_type": "ach", "depends_on": ["a"]},
        ]
        flow = self._make_flow_dict(steps)
        compute_effective_dates(flow, seed=1)
        date_a = date.fromisoformat(flow["steps"][0]["effective_date"])
        date_b = date.fromisoformat(flow["steps"][1]["effective_date"])
        assert date_b >= date_a

    def test_ledger_transaction_gets_effective_at(self):
        steps = [
            {"step_id": "lt", "type": "ledger_transaction", "depends_on": []},
        ]
        flow = self._make_flow_dict(steps)
        compute_effective_dates(flow, seed=1)
        assert flow["steps"][0].get("effective_at") is not None
        assert flow["steps"][0].get("effective_date") is not None

    def test_computed_dates_metadata(self):
        steps = [
            {"step_id": "a", "type": "payment_order", "payment_type": "book", "depends_on": []},
        ]
        flow = self._make_flow_dict(steps)
        compute_effective_dates(flow, seed=1)
        assert "_computed_dates" in flow
        assert "a" in flow["_computed_dates"]
        assert "_base_date" in flow

    def test_recipe_timing_with_overrides(self):
        steps = [
            {"step_id": "a", "type": "payment_order", "payment_type": "book", "depends_on": []},
        ]
        flow = self._make_flow_dict(steps)
        recipe = RecipeTimingConfig(
            step_delay_overrides={"a": 120.0},  # 5 days in hours
        )
        compute_effective_dates(flow, recipe_timing=recipe, seed=1)
        computed = date.fromisoformat(flow["steps"][0]["effective_date"])
        # 120h = 5 days → 5 business days from today
        expected = _advance_business_days(date.today(), 5)
        assert computed == expected

    def test_deterministic_jitter(self):
        def run():
            steps = [
                {"step_id": "a", "type": "payment_order", "payment_type": "book",
                 "depends_on": [], "timing": {"delay_hours": 120, "delay_jitter_hours": 48}},
            ]
            flow = {"steps": steps}
            compute_effective_dates(flow, seed=42)
            return flow["steps"][0]["effective_date"]

        assert run() == run()
