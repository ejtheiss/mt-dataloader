"""Timing, seasoning, and date configuration for funds flow instances.

Resolution order for step delay (most specific wins):
  recipe.step_delay_overrides[step_id]   (hours)
  → step.timing.delay_hours
  → flow.timing.default_delay_hours
  → settlement_defaults (direction-aware, step-type-aware, hours)
  → 0

All delays are specified in **hours** and converted to calendar days
internally (hours / 24, rounded).  Settlement defaults are configurable
via ``SettlementDefaultsConfig`` on ``FlowTimingConfig.settlement_defaults``.
Step types in ``no_delay_step_types`` (IPDs, EPs, LTs) always resolve to 0.
Lookup keys can be direction-specific (``"ach:debit"``) or generic (``"ach"``).

All date math uses plain calendar days.  This is a demo environment —
timing is configuration, not real-world simulation.
"""

from __future__ import annotations

import random
from datetime import date, timedelta
from typing import Literal

from models.flow_dsl import FlowTimingConfig, RecipeTimingConfig
from models.shared import SettlementDefaultsConfig

_SETTLEMENT_DEFAULTS = SettlementDefaultsConfig()


def _hours_to_days(hours: float) -> int:
    """Convert hours to whole calendar days, rounding to nearest."""
    return max(0, round(hours / 24.0))


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
        offsets = [spread_days * ((i / max(instances - 1, 1)) ** 2) for i in range(instances)]

    elif pattern == "ramp_down":
        offsets = [
            spread_days * (1 - ((max(instances - 1, 1) - i) / max(instances - 1, 1)) ** 2)
            for i in range(instances)
        ]

    elif pattern == "clustered":
        cluster_centers = [0.0, spread_days / 2, float(spread_days)]
        offsets = []
        for i in range(instances):
            center = cluster_centers[i % len(cluster_centers)]
            offsets.append(center)
        offsets.sort()

    else:
        offsets = [0.0] * instances

    if jitter_days > 0:
        offsets = [max(0.0, o + rng.uniform(-jitter_days, jitter_days)) for o in offsets]

    return offsets


def _resolve_step_delay(
    step: dict,
    flow_timing: FlowTimingConfig | None,
    recipe_overrides: dict[str, float] | None,
) -> tuple[float, float]:
    """Resolve delay (hours) and jitter (hours) for a step.

    Precedence (most specific wins):
    1. recipe.step_delay_overrides[step_id]  (hours)
    2. step.timing                           (hours)
    3. flow.timing defaults                  (hours)
    4. settlement_defaults                   (hours, direction-aware, step-type-aware)
    """
    step_id = step.get("step_id", "")
    step_timing_dict = step.get("timing")

    if recipe_overrides and step_id in recipe_overrides:
        delay = recipe_overrides[step_id]
        jitter = 0.0
        if step_timing_dict:
            jitter = step_timing_dict.get("delay_jitter_hours", 0.0)
        elif flow_timing:
            jitter = flow_timing.default_jitter_hours
        return delay, jitter

    if step_timing_dict:
        return (
            step_timing_dict.get("delay_hours", 0.0),
            step_timing_dict.get("delay_jitter_hours", 0.0),
        )

    if flow_timing and (
        flow_timing.default_delay_hours > 0 or flow_timing.default_jitter_hours > 0
    ):
        return (
            flow_timing.default_delay_hours,
            flow_timing.default_jitter_hours,
        )

    defaults = flow_timing.settlement_defaults if flow_timing else _SETTLEMENT_DEFAULTS
    step_type = step.get("type", "")
    payment_type = step.get("payment_type", "")
    direction = step.get("direction", "")

    delay = defaults.lookup_settlement(payment_type, direction, step_type)
    return delay, 0.0


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

    When ``recipe_timing.start_date`` is set **without** ``flow_timing``,
    the date is used directly — no settlement delays are applied.  This gives
    the user exact control over when payments originate.  Steps still respect
    ``step_offsets`` and ``depends_on`` ordering.

    Settlement delays only kick in when ``flow_timing`` is provided (which
    implies the flow author opted in to realistic timing).
    """
    has_explicit_start = bool(recipe_timing and recipe_timing.start_date)
    base = recipe_timing.start_date if has_explicit_start else date.today()

    recipe_overrides = recipe_timing.step_delay_overrides if recipe_timing else None
    step_offsets = recipe_timing.step_offsets if recipe_timing else None

    # When the user provides explicit date control (start_date or step
    # offsets) without flow-level timing, skip settlement delays so dates
    # land exactly where the user expects.
    has_step_offsets = bool(step_offsets)
    skip_settlement = (has_explicit_start or has_step_offsets) and not flow_timing

    rng = random.Random(seed + instance_index + 5555)

    if spread_offset_days > 0:
        whole_days = int(spread_offset_days)
        base = base + timedelta(days=whole_days)

    all_steps = list(flow_dict.get("steps") or [])

    step_dates: dict[str, date] = {}

    for step in all_steps:
        step_id = step.get("step_id", "")

        if step_offsets and step_id in step_offsets:
            offset_days = step_offsets[step_id]
            effective = base + timedelta(days=offset_days) if offset_days > 0 else base
            step_dates[step_id] = effective
        elif skip_settlement:
            dep_base = base
            for dep_id in step.get("depends_on", []):
                if dep_id in step_dates and step_dates[dep_id] > dep_base:
                    dep_base = step_dates[dep_id]
            step_dates[step_id] = dep_base
            effective = dep_base
        else:
            dep_base = base
            for dep_id in step.get("depends_on", []):
                if dep_id in step_dates and step_dates[dep_id] > dep_base:
                    dep_base = step_dates[dep_id]

            delay_hours, jitter_hours = _resolve_step_delay(
                step,
                flow_timing,
                recipe_overrides,
            )

            if jitter_hours > 0:
                delay_hours += rng.uniform(-jitter_hours, jitter_hours)
                delay_hours = max(0.0, delay_hours)

            delay_int = _hours_to_days(delay_hours)

            if delay_int > 0:
                effective = dep_base + timedelta(days=delay_int)
            else:
                effective = dep_base

            step_dates[step_id] = effective
        effective_str = effective.isoformat()

        step_type = step.get("type", "")
        if step_type == "payment_order":
            if not step.get("effective_date"):
                step["effective_date"] = effective_str

        if step_type in ("ledger_transaction", "transition_ledger_transaction"):
            if not step.get("effective_at"):
                step["effective_at"] = effective_str
            if not step.get("effective_date"):
                step["effective_date"] = effective_str

    flow_dict["_computed_dates"] = {sid: d.isoformat() for sid, d in step_dates.items()}
    flow_dict["_base_date"] = base.isoformat()

    return flow_dict
