"""Timing, seasoning, and date configuration for funds flow instances.

Resolution order (most specific wins):
  recipe.step_delay_overrides[step_id]
  → step.timing.delay_days
  → optional_group.timing.delay_days (if step came from a group)
  → flow.timing.default_delay_days
  → payment-type default (ACH 2d, wire 0d, …)
  → 0

Jitter is added *after* the delay is resolved, then business-day skipping
applies to the final date.
"""

from __future__ import annotations

import math
import random
from datetime import date, timedelta
from typing import Literal

from models.flow_dsl import FlowTimingConfig, RecipeTimingConfig
from models.shared import StepTimingConfig

# ---------------------------------------------------------------------------
# Payment-type default settlement delays (business days)
# ---------------------------------------------------------------------------

_DEFAULT_SETTLEMENT_DAYS: dict[str, float] = {
    "ach": 2.0,
    "wire": 0.0,
    "book": 0.0,
    "rtp": 0.0,
    "check": 5.0,
    "eft": 3.0,
    "sepa": 1.0,
    "bacs": 3.0,
}

_DEFAULT_RETURN_DAYS: dict[str, float] = {
    "ach": 3.0,
    "wire": 5.0,
    "book": 0.0,
    "check": 10.0,
}

# US federal holidays (month, day) — static set; no bank-holiday API needed.
_US_HOLIDAYS: set[tuple[int, int]] = {
    (1, 1),    # New Year
    (1, 20),   # MLK (approximate — third Monday; we use a fixed date)
    (2, 17),   # Presidents' Day (approximate)
    (5, 26),   # Memorial Day (approximate)
    (6, 19),   # Juneteenth
    (7, 4),    # Independence Day
    (9, 1),    # Labor Day (approximate)
    (10, 13),  # Columbus Day (approximate)
    (11, 11),  # Veterans Day
    (11, 27),  # Thanksgiving (approximate)
    (12, 25),  # Christmas
}


def _is_business_day(d: date) -> bool:
    return d.weekday() < 5 and (d.month, d.day) not in _US_HOLIDAYS


def _advance_business_days(start: date, days: int) -> date:
    """Move forward by *days* business days from *start*."""
    if days <= 0:
        return start
    current = start
    remaining = days
    while remaining > 0:
        current += timedelta(days=1)
        if _is_business_day(current):
            remaining -= 1
    return current


def _skip_to_business_day(d: date) -> date:
    """If *d* falls on a weekend/holiday, roll forward to the next business day."""
    while not _is_business_day(d):
        d += timedelta(days=1)
    return d


def _parse_base_date(spec: str) -> date:
    """Parse a base_date string into a ``date``.

    Accepts:
      - ``"today"``
      - ``"YYYY-MM-DD"``
      - ``"today-Nd"`` (e.g. ``"today-30d"``)
    """
    spec = spec.strip().lower()
    if spec == "today":
        return date.today()
    if spec.startswith("today-") and spec.endswith("d"):
        n = int(spec[6:-1])
        return date.today() - timedelta(days=n)
    return date.fromisoformat(spec)


# ---------------------------------------------------------------------------
# Spread patterns — deterministic for a given (seed, instances, spread_days)
# ---------------------------------------------------------------------------


def compute_spread_offsets(
    instances: int,
    spread_days: int,
    pattern: Literal["uniform", "ramp_up", "ramp_down", "clustered"],
    seed: int,
    jitter_days: float = 0.0,
) -> list[float]:
    """Return per-instance day offsets from the base date.

    All patterns are fully deterministic for a given seed.
    """
    if instances <= 0:
        return []
    if spread_days <= 0:
        return [0.0] * instances

    rng = random.Random(seed + 9999)

    if pattern == "uniform":
        if instances == 1:
            offsets = [0.0]
        else:
            step = spread_days / (instances - 1)
            offsets = [i * step for i in range(instances)]

    elif pattern == "ramp_up":
        # Quadratic ramp — early instances clustered near 0, later ones spread out.
        offsets = [
            spread_days * ((i / max(instances - 1, 1)) ** 2) for i in range(instances)
        ]

    elif pattern == "ramp_down":
        # Inverse quadratic — early instances spread, later ones clustered near end.
        offsets = [
            spread_days * (1 - ((max(instances - 1, 1) - i) / max(instances - 1, 1)) ** 2)
            for i in range(instances)
        ]

    elif pattern == "clustered":
        # 3 clusters at 0%, 50%, 100% of spread_days.
        cluster_centers = [0.0, spread_days / 2, float(spread_days)]
        offsets = []
        for i in range(instances):
            center = cluster_centers[i % len(cluster_centers)]
            offsets.append(center)
        offsets.sort()

    else:
        offsets = [0.0] * instances

    if jitter_days > 0:
        offsets = [
            max(0.0, o + rng.uniform(-jitter_days, jitter_days)) for o in offsets
        ]

    return offsets


def _resolve_step_delay(
    step: dict,
    flow_timing: FlowTimingConfig | None,
    recipe_overrides: dict[str, float] | None,
) -> tuple[float, float, bool]:
    """Resolve delay_days, jitter, and business_days_only for a single step.

    Precedence (most specific wins):
    1. recipe.step_delay_overrides[step_id]
    2. step.timing
    3. flow.timing defaults
    4. payment-type defaults
    """
    step_id = step.get("step_id", "")
    step_timing_dict = step.get("timing")

    if recipe_overrides and step_id in recipe_overrides:
        delay = recipe_overrides[step_id]
        jitter = 0.0
        biz_days = True
        if step_timing_dict:
            jitter = step_timing_dict.get("delay_jitter_days", 0.0)
            biz_days = step_timing_dict.get("business_days_only", True)
        elif flow_timing:
            jitter = flow_timing.default_jitter_days
            biz_days = flow_timing.business_days_only
        return delay, jitter, biz_days

    if step_timing_dict:
        return (
            step_timing_dict.get("delay_days", 0.0),
            step_timing_dict.get("delay_jitter_days", 0.0),
            step_timing_dict.get("business_days_only", True),
        )

    if flow_timing and (flow_timing.default_delay_days > 0 or flow_timing.default_jitter_days > 0):
        return (
            flow_timing.default_delay_days,
            flow_timing.default_jitter_days,
            flow_timing.business_days_only,
        )

    # Payment-type defaults
    step_type = step.get("type", "")
    payment_type = step.get("payment_type", "")

    if step_type == "return":
        delay = _DEFAULT_RETURN_DAYS.get(payment_type, 0.0)
    else:
        delay = _DEFAULT_SETTLEMENT_DAYS.get(payment_type, 0.0)

    return delay, 0.0, True


def compute_effective_dates(
    flow_dict: dict,
    *,
    instance_index: int = 0,
    spread_offset_days: float = 0.0,
    flow_timing: FlowTimingConfig | None = None,
    recipe_timing: RecipeTimingConfig | None = None,
    seed: int = 0,
) -> dict:
    """Stamp ``effective_date`` and ``effective_at`` on steps in *flow_dict*.

    Mutates and returns *flow_dict* for chaining.  Steps that already have an
    explicit ``effective_date`` or ``effective_at`` are left untouched.
    """
    base_date_spec = "today"
    if recipe_timing and recipe_timing.base_date:
        base_date_spec = recipe_timing.base_date
    elif flow_timing and flow_timing.base_date:
        base_date_spec = flow_timing.base_date

    base = _parse_base_date(base_date_spec)

    recipe_overrides = (
        recipe_timing.step_delay_overrides if recipe_timing else None
    )

    rng = random.Random(seed + instance_index + 5555)

    # Apply spread offset to the base date
    if spread_offset_days > 0:
        whole_days = int(spread_offset_days)
        base = base + timedelta(days=whole_days)

    all_steps = list(flow_dict.get("steps") or [])

    # Build step_id -> computed date for depends_on chaining
    step_dates: dict[str, date] = {}

    for step in all_steps:
        step_id = step.get("step_id", "")

        # Find the latest dependency date as the starting point
        dep_base = base
        for dep_id in step.get("depends_on", []):
            if dep_id in step_dates and step_dates[dep_id] > dep_base:
                dep_base = step_dates[dep_id]

        delay, jitter, biz_days = _resolve_step_delay(
            step, flow_timing, recipe_overrides,
        )

        if jitter > 0:
            delay += rng.uniform(-jitter, jitter)
            delay = max(0.0, delay)

        delay_int = max(0, round(delay))

        if biz_days and delay_int > 0:
            effective = _advance_business_days(dep_base, delay_int)
        elif delay_int > 0:
            effective = dep_base + timedelta(days=delay_int)
        else:
            effective = dep_base

        if biz_days:
            effective = _skip_to_business_day(effective)

        step_dates[step_id] = effective
        effective_str = effective.isoformat()

        # Only stamp if the step doesn't already have an explicit value
        step_type = step.get("type", "")
        if step_type in (
            "payment_order", "expected_payment",
            "incoming_payment_detail", "return", "reversal",
        ):
            if not step.get("effective_date"):
                step["effective_date"] = effective_str

        if step_type in ("ledger_transaction", "transition_ledger_transaction"):
            if not step.get("effective_at"):
                step["effective_at"] = effective_str
            if not step.get("effective_date"):
                step["effective_date"] = effective_str

    # Store computed day offsets for UI display
    flow_dict["_computed_dates"] = {
        sid: d.isoformat() for sid, d in step_dates.items()
    }
    flow_dict["_base_date"] = base.isoformat()

    return flow_dict
