"""Flow-level DSL types: FundsFlowConfig, view configs, and generation models."""

from __future__ import annotations

import string
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Timing configuration (Step 9: Seasoning & Date Configuration)
# ---------------------------------------------------------------------------
from models.shared import SettlementDefaultsConfig  # noqa: E402 (re-export)


class FlowTimingConfig(BaseModel):
    """Default timing applied to all steps in a flow."""

    model_config = ConfigDict(extra="forbid")

    default_delay_hours: float = 0.0
    default_jitter_hours: float = 0.0
    settlement_defaults: SettlementDefaultsConfig = Field(
        default_factory=SettlementDefaultsConfig,
        description="Per-rail settlement delays (hours) and step-type exclusions",
    )


class RecipeTimingConfig(BaseModel):
    """Timing controls for scaled generation (instance spread + overrides)."""

    model_config = ConfigDict(extra="forbid")

    start_date: date | None = Field(
        default=None,
        description="Anchor date for the first step (defaults to today). "
        "Set to a past date to ensure payments settle immediately.",
    )
    instance_spread_days: int = 0
    spread_pattern: Literal["uniform", "ramp_up", "ramp_down", "clustered"] = "uniform"
    spread_jitter_days: float = 0.0
    step_delay_overrides: dict[str, float] = Field(
        default_factory=dict,
        description="step_id -> delay_hours override for this recipe",
    )
    step_offsets: dict[str, int] = Field(
        default_factory=dict,
        description="step_id -> T+N business-day offset from the scenario builder UI",
    )


from models.steps import (  # noqa: E402 — after FlowTimingConfig / RecipeTimingConfig (circular import guard)
    FundsFlowStep,
    IncomingPaymentDetailStep,
    ReturnStep,
    ReversalStep,
    _extract_step_ref,
    _StepBase,
)


class StepMatch(BaseModel):
    """Conjunctive match: all non-None conditions must hold on the SAME step."""

    model_config = ConfigDict(extra="forbid")

    payment_type: str | None = None
    direction: str | None = None
    resource_type: str | None = None


class ApplicabilityRule(BaseModel):
    """When should an optional group be considered for activation?

    ``requires_step_match`` — at least one step must satisfy ALL conditions
    in at least one StepMatch.

    ``excludes_step_match`` — NO step may satisfy ALL conditions in any
    StepMatch (used for "R01 never applies to RTP" type rules).

    ``depends_on_step`` — the named step_id must exist in the flow.
    """

    model_config = ConfigDict(extra="forbid")

    requires_step_match: list[StepMatch] | None = None
    excludes_step_match: list[StepMatch] | None = None
    depends_on_step: str | None = None


class OptionalGroupConfig(BaseModel):
    """An optional lifecycle branch within a funds flow pattern.

    Groups with the same ``exclusion_group`` are mutually exclusive —
    at most one will activate per instance (e.g. "RTP payout" vs "Wire
    payout").  Groups without an exclusion_group are independent.

    trigger is documentation/rendering metadata only — no execution impact.
    """

    model_config = ConfigDict(extra="forbid")

    label: str
    trigger: Literal["manual", "system", "webhook"] = "manual"
    exclusion_group: str | None = Field(
        default=None,
        description="Groups sharing the same exclusion_group are mutually exclusive.",
    )
    steps: list[FundsFlowStep] = Field(..., min_length=1)
    position: Literal["before", "after", "replace"] = "after"
    insert_after: str | None = Field(
        default=None,
        description=(
            "Anchor step_id. 'after'+anchor inserts after that step; "
            "'before'+anchor inserts before it; 'replace'+anchor removes "
            "the anchor step and inserts the group's steps in its place."
        ),
    )
    applicable_when: ApplicabilityRule | None = None
    weight: float = Field(
        default=1.0,
        ge=0.0,
        description="Relative weight within an exclusion_group for weighted selection.",
    )


class FundsFlowScaleConfig(BaseModel):
    """Expansion settings for generating multiple instances of a flow."""

    model_config = ConfigDict(extra="forbid")

    instances: int = Field(1, ge=1, le=5000)
    seed_namespace: str | None = None


_TRACE_FORMATTER = string.Formatter()
_ALLOWED_TRACE_PLACEHOLDERS = frozenset(
    {
        "ref",
        "instance",
        "first_name",
        "last_name",
        "business_name",
        "industry",
        "country",
    }
)


class LedgerViewConfig(BaseModel):
    """Configuration for the ledger-centric view."""

    model_config = ConfigDict(extra="forbid")

    ledger_ref: str | None = None
    metadata_key: str | None = None
    account_columns: list[str] = Field(
        default_factory=list,
        description="Ordered list of ledger account refs to show as columns",
    )


class PaymentsViewConfig(BaseModel):
    """Configuration for the payments-centric view."""

    model_config = ConfigDict(extra="forbid")

    account_columns: list[str] = Field(
        default_factory=list,
        description=(
            "Ordered list of account refs to show as columns. "
            "Supports IA refs, EA refs, and VA refs."
        ),
    )
    include_expected_payments: bool = True
    include_transactions: bool = False


class FundFlowViewConfig(BaseModel):
    """Declares which views are available for this flow."""

    model_config = ConfigDict(extra="forbid")

    ledger_view: LedgerViewConfig | None = None
    payments_view: PaymentsViewConfig | None = None


# ---------------------------------------------------------------------------
# Actor Frames & Slots (Plan 3 Phase 1)
# ---------------------------------------------------------------------------


class ActorSlot(BaseModel):
    """A single account/resource slot on an actor frame."""

    model_config = ConfigDict(extra="forbid")

    ref: str
    slot_type: (
        Literal[
            "external_account",
            "internal_account",
            "ledger_account",
            "virtual_account",
        ]
        | None
    ) = Field(
        default=None,
        description="When None, inferred from $ref: prefix.",
    )
    fi: str | None = Field(
        default=None,
        description=(
            "Financial institution label. For BYOB IAs: the bank the IA "
            "lives at (e.g. 'Wells Fargo', 'Lead'). For EAs: the CP's "
            "bank (e.g. 'JPMC'). Display and future auto-generation."
        ),
    )


class ActorFrame(BaseModel):
    """A typed participant frame with named account slots.

    The Frame is the identity anchor for a participant. It owns exactly
    one name source — either faker-seeded (via entity_ref -> LE) or
    literal (via customer_name). That name cascades to every nested
    resource: CP name, EA party_name, IA name/party_name, LA name.
    """

    model_config = ConfigDict(extra="forbid")

    alias: str
    frame_type: Literal["user", "direct"] = "user"

    entity_ref: str | None = Field(
        default=None,
        description=(
            "LE reference (e.g. '$ref:legal_entity.buyer_{instance}'). "
            "The LE is where faker-seeded names land; that name then "
            "cascades to all CPs, EAs, IAs, and LAs owned by this frame."
        ),
    )
    customer_name: str | None = Field(
        default=None,
        description=(
            "Literal name for direct frames (e.g. 'Boats Group'). "
            "Replaces faker data as the name source."
        ),
    )

    slots: dict[str, ActorSlot | str] = Field(default_factory=dict)

    dataset: str | None = None
    name_template: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _promote_slot_strings(cls, data: dict) -> dict:
        """Bare strings in slots are promoted to ActorSlot(ref=...)."""
        if isinstance(data, dict):
            slots = data.get("slots", {})
            for name, val in slots.items():
                if isinstance(val, str):
                    slots[name] = {"ref": val}
        return data


class FundsFlowConfig(BaseModel):
    """High-level funds flow definition — compiler input, not an MT resource."""

    model_config = ConfigDict(extra="forbid")

    ref: str
    pattern_type: str
    trace_key: str = "deal_id"
    trace_metadata: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "String templates expanded per instance; merged onto every step's MT metadata. "
            "The entry at trace_key is the primary correlation id template (formerly trace_value_template)."
        ),
    )
    actors: dict[str, ActorFrame] = Field(default_factory=dict)
    steps: list[FundsFlowStep] = Field(..., min_length=1)
    optional_groups: list[OptionalGroupConfig] = Field(default_factory=list)
    scale: FundsFlowScaleConfig | None = None
    view_config: FundFlowViewConfig | None = None
    timing: FlowTimingConfig | None = None
    instance_resources: dict[str, list[dict[str, Any]]] | None = Field(
        default=None,
        description=(
            "Per-instance infrastructure templates keyed by resource section. "
            "Each template dict is cloned per instance with {placeholder} "
            "substitution from seed profiles."
        ),
    )
    display_title: str | None = Field(
        default=None,
        max_length=120,
        description="Operator-visible title; does not affect compilation.",
    )
    display_summary: str | None = Field(
        default=None,
        max_length=500,
        description="Short operator-visible summary; does not affect compilation.",
    )

    @field_validator("display_title", "display_summary", mode="before")
    @classmethod
    def _strip_display_fields(cls, v: Any) -> Any:
        if v is None:
            return None
        if isinstance(v, str):
            s = v.strip()
            return s or None
        return v

    @model_validator(mode="before")
    @classmethod
    def _migrate_trace_value_template(cls, data: Any) -> Any:
        """Accept legacy ``trace_value_template`` by folding it into ``trace_metadata[trace_key]``."""
        if not isinstance(data, dict):
            return data
        tk = data.get("trace_key", "deal_id")
        tm = dict(data.get("trace_metadata") or {})
        legacy = data.pop("trace_value_template", None)
        if legacy is not None and (tk not in tm or not str(tm.get(tk, "")).strip()):
            tm[tk] = legacy
        if tk not in tm or not str(tm.get(tk, "")).strip():
            tm[tk] = "{ref}-{instance}"
        data["trace_metadata"] = tm
        return data

    @model_validator(mode="after")
    def _validate_flow(self) -> FundsFlowConfig:
        all_steps: list[_StepBase] = list(self.steps)
        for og in self.optional_groups:
            all_steps.extend(og.steps)

        ids = [s.step_id for s in all_steps]
        dupes = {sid for sid in ids if ids.count(sid) > 1}
        if dupes:
            raise ValueError(f"Duplicate step_id(s): {dupes}")

        tpl = self.trace_metadata[self.trace_key]
        try:
            fields = {fname for _, fname, _, _ in _TRACE_FORMATTER.parse(tpl) if fname is not None}
        except (ValueError, KeyError) as e:
            raise ValueError(
                f"Invalid trace template at trace_metadata['{self.trace_key}']: {e}"
            ) from e
        bad = fields - _ALLOWED_TRACE_PLACEHOLDERS
        if bad:
            raise ValueError(
                f"trace_metadata['{self.trace_key}'] contains unknown placeholders: {bad}. "
                f"Allowed: {sorted(_ALLOWED_TRACE_PLACEHOLDERS)}"
            )

        step_ids = set(ids)
        for step in all_steps:
            for dep in step.depends_on:
                if dep not in step_ids:
                    raise ValueError(
                        f"Step '{step.step_id}' depends_on '{dep}' "
                        f"which is not a valid step_id in this flow"
                    )

        for step in all_steps:
            if isinstance(step, ReturnStep) and step.returnable_id:
                target = _extract_step_ref(step.returnable_id)
                if target and target not in step_ids:
                    raise ValueError(
                        f"Return '{step.step_id}' references unknown returnable target '{target}'"
                    )

        for step in all_steps:
            if isinstance(step, ReversalStep) and step.payment_order_id:
                target = _extract_step_ref(step.payment_order_id)
                if target and target not in step_ids:
                    raise ValueError(
                        f"Reversal '{step.step_id}' references unknown PO target '{target}'"
                    )

        for step in all_steps:
            if isinstance(step, IncomingPaymentDetailStep) and step.fulfills:
                target = _extract_step_ref(step.fulfills)
                if target and target not in step_ids:
                    raise ValueError(f"IPD '{step.step_id}' fulfills unknown EP '{target}'")

        return self


# ---------------------------------------------------------------------------
# Generation pipeline models
# ---------------------------------------------------------------------------


class PaymentMixConfig(BaseModel):
    """Controls which resource types to include when generating instances."""

    model_config = ConfigDict(extra="forbid")

    include_expected_payments: bool = True
    include_payment_orders: bool = True
    include_ipds: bool = True
    include_returns: bool = True
    include_reversals: bool = True
    include_standalone_lts: bool = True


class ActorDatasetOverride(BaseModel):
    """Per-actor override for seed generation in a recipe."""

    model_config = ConfigDict(extra="forbid")

    dataset: str | None = None
    entity_type: Literal["business", "individual"] | None = Field(
        default=None,
        description="Whether this actor uses business or individual seed profiles",
    )
    customer_name: str | None = Field(
        default=None,
        description="Literal name override (e.g. 'Tradeify'). Takes priority over faker.",
    )
    name_template: str | None = None


class EdgeCaseOverride(BaseModel):
    """Per-group override in the generation recipe."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    count: int | None = Field(
        default=None,
        ge=0,
        description="Exact number of instances that get this edge case (None = use global edge_case_count)",
    )
    value_overrides: dict[str, Any] = Field(default_factory=dict)


class StagingRule(BaseModel):
    """One staging condition — stage `count` instances from `selection`."""

    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=0, description="Number of instances to stage from this pool")
    selection: str = Field(
        default="happy_path",
        description=(
            "'happy_path' — instances without edge cases; "
            "'all' — first N regardless; "
            "or an edge case label"
        ),
    )


class GenerationRecipeV1(BaseModel):
    """Compact, UI-facing recipe for generating N flow instances."""

    model_config = ConfigDict(extra="forbid")

    version: Literal["v1"] = "v1"
    flow_ref: str
    instances: int = Field(..., ge=1, le=5000)
    seed: int
    seed_dataset: str = Field(
        default="standard",
        description="Default seed dataset (used when per-actor fields are omitted)",
    )
    business_dataset: str | None = Field(
        default=None,
        description="Seed dataset for business profiles (overrides seed_dataset)",
    )
    individual_dataset: str | None = Field(
        default=None,
        description="Seed dataset for individual profiles (overrides seed_dataset)",
    )
    edge_case_count: int = Field(
        default=0,
        ge=0,
        description="Default number of instances that get each edge case (capped at instances)",
    )
    edge_case_overrides: dict[str, EdgeCaseOverride] = Field(
        default_factory=dict,
        description="Per-group overrides keyed by optional group label",
    )
    amount_variance_min_pct: float = Field(
        default=0.0,
        ge=-100.0,
        le=0.0,
        description="Minimum percentage variance on amounts (e.g. -10.0 = down to 90% of base)",
    )
    amount_variance_max_pct: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Maximum percentage variance on amounts (e.g. 10.0 = up to 110% of base)",
    )
    step_variance: dict[str, dict[str, float]] = Field(
        default_factory=dict,
        description=(
            "Per-step variance overrides keyed by step_id. "
            'Each value is {"min_pct": ..., "max_pct": ...} or empty dict to lock (no variance).'
        ),
    )
    staging_rules: list[StagingRule] = Field(
        default_factory=list,
        description=(
            "Each rule stages `count` instances drawn from `selection`. "
            "Multiple rules are unioned — e.g. 3 happy-path + 2 late-return."
        ),
    )
    staged_count: int = Field(
        default=0,
        ge=0,
        description="(Legacy) Shorthand for a single staging rule — prefer staging_rules.",
    )
    staged_selection: str = Field(
        default="happy_path",
        description="(Legacy) Selection for the single staging rule shorthand.",
    )
    payment_mix: PaymentMixConfig | None = None
    actor_overrides: dict[str, ActorDatasetOverride] = Field(
        default_factory=dict,
        description="Per-actor overrides keyed by frame name (e.g. 'user_1')",
    )
    timing: RecipeTimingConfig | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize(self) -> GenerationRecipeV1:
        if self.edge_case_count > self.instances:
            self.edge_case_count = self.instances
        for ov in self.edge_case_overrides.values():
            if ov.count is not None and ov.count > self.instances:
                ov.count = self.instances

        # Legacy: promote staged_count/staged_selection into staging_rules
        if not self.staging_rules and self.staged_count > 0:
            self.staging_rules = [
                StagingRule(count=self.staged_count, selection=self.staged_selection),
            ]
        return self
