"""Funds Flow DSL — Typed Step Models (Plan 0: Discriminated Union).

Each step type is a Pydantic model with a ``type`` literal discriminator.
``FundsFlowStep`` is the discriminated union consumed by ``FundsFlowConfig``.
Derived constants (``VALID_STEP_TYPES``, ``ARROW_BY_TYPE``, etc.) are
auto-computed from the union members — adding a new step type only requires
one new model class.
"""

from __future__ import annotations

from typing import Annotated, ClassVar, Literal, Union, get_args

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from models.shared import (
    AmountCents,
    CurrencyCode,
    InlineLedgerEntryConfig,
    LedgerStatus,
    MetadataMixin,
    PaymentDirection,
    StepTimingConfig,
)

# ---------------------------------------------------------------------------
# Ledger balance check (shared by mixins below)
# ---------------------------------------------------------------------------


def _check_ledger_entries_balanced(
    step_id: str, entries: list[InlineLedgerEntryConfig] | None,
) -> None:
    if not entries:
        return
    debits = sum(e.amount for e in entries if e.direction == "debit")
    credits_ = sum(e.amount for e in entries if e.direction == "credit")
    if debits != credits_:
        raise ValueError(
            f"Step '{step_id}' ledger_entries unbalanced: "
            f"debits={debits}, credits={credits_}"
        )


# ---------------------------------------------------------------------------
# Step base + ledger mixins
# ---------------------------------------------------------------------------


class _StepBase(MetadataMixin, BaseModel):
    """Shared fields across all fund-flow step types."""

    model_config = ConfigDict(extra="forbid")

    step_id: str
    description: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    timing: StepTimingConfig | None = None

    _config_section: ClassVar[str]
    _mermaid_arrow: ClassVar[str] = "->>"
    _reverses_direction: ClassVar[bool] = False
    _is_lifecycle_type: ClassVar[bool] = False
    _is_ledgerable: ClassVar[bool] = False
    _payment_mix_field: ClassVar[str | None] = None


class _LedgerableMixin(BaseModel):
    """Shared ledger fields for steps that can carry inline LTs.

    Applied to PO, IPD, EP, and LT step types. Validates that ledger
    entries are balanced (total debits == total credits).
    """

    ledger_entries: list[InlineLedgerEntryConfig] | None = None
    ledger_inline: bool = False
    ledger_status: LedgerStatus | None = None

    @model_validator(mode="after")
    def _validate_ledger_balance(self):
        _check_ledger_entries_balanced(self.step_id, self.ledger_entries)
        return self


class _LifecycleLedgerMixin(BaseModel):
    """Ledger fields for lifecycle steps (Return, Reversal).

    Allows ``"reverse_parent"`` as an alternative to an explicit entry
    list. Skips balance validation (the compiler resolves reverse_parent
    at compile time).
    """

    ledger_entries: (
        list[InlineLedgerEntryConfig] | Literal["reverse_parent"] | None
    ) = None
    ledger_inline: bool = False
    ledger_status: LedgerStatus | None = None


# ---------------------------------------------------------------------------
# Concrete step types
# ---------------------------------------------------------------------------


class PaymentOrderStep(_LedgerableMixin, _StepBase):
    type: Literal["payment_order"]
    payment_type: str
    direction: PaymentDirection
    amount: AmountCents
    originating_account_id: str
    receiving_account_id: str | None = None
    currency: CurrencyCode = None
    statement_descriptor: str | None = None
    effective_date: str | None = None
    staged: bool = False

    _config_section: ClassVar[str] = "payment_orders"
    _mermaid_arrow: ClassVar[str] = "-)"
    _is_ledgerable: ClassVar[bool] = True
    _payment_mix_field: ClassVar[str | None] = "include_payment_orders"

    @model_validator(mode="after")
    def _credit_needs_receiver(self):
        if self.direction == "credit" and not self.receiving_account_id:
            raise ValueError(
                f"PO step '{self.step_id}': direction='credit' requires "
                f"receiving_account_id"
            )
        return self


class IncomingPaymentDetailStep(_LedgerableMixin, _StepBase):
    type: Literal["incoming_payment_detail"]
    payment_type: str
    amount: AmountCents
    originating_account_id: str | None = None
    internal_account_id: str
    direction: Literal["credit"] = "credit"
    currency: CurrencyCode = None
    virtual_account_id: str | None = None
    as_of_date: str | None = None
    fulfills: str | None = None
    staged: bool = False

    _config_section: ClassVar[str] = "incoming_payment_details"
    _mermaid_arrow: ClassVar[str] = "->>"
    _is_ledgerable: ClassVar[bool] = True
    _payment_mix_field: ClassVar[str | None] = "include_ipds"


class ExpectedPaymentStep(_LedgerableMixin, _StepBase):
    type: Literal["expected_payment"]
    amount: int | None = None
    direction: PaymentDirection | None = None
    originating_account_id: str | None = None
    internal_account_id: str | None = None
    currency: CurrencyCode = None
    date_lower_bound: str | None = None
    date_upper_bound: str | None = None
    staged: bool = False

    _config_section: ClassVar[str] = "expected_payments"
    _mermaid_arrow: ClassVar[str] = "->>"
    _is_ledgerable: ClassVar[bool] = True
    _payment_mix_field: ClassVar[str | None] = "include_expected_payments"


class LedgerTransactionStep(_StepBase):
    """Standalone ledger transaction — ledger_entries is required, no ledger_inline."""

    type: Literal["ledger_transaction"]
    ledger_entries: list[InlineLedgerEntryConfig] = Field(..., min_length=1)
    ledger_status: LedgerStatus | None = None
    effective_at: str | None = None
    effective_date: str | None = None
    staged: bool = False

    _config_section: ClassVar[str] = "ledger_transactions"
    _mermaid_arrow: ClassVar[str] = "->>"
    _payment_mix_field: ClassVar[str | None] = "include_standalone_lts"

    @model_validator(mode="after")
    def _validate_ledger_balance(self):
        _check_ledger_entries_balanced(self.step_id, self.ledger_entries)
        return self


class ReturnStep(_LifecycleLedgerMixin, _StepBase):
    type: Literal["return"]
    returnable_id: str | None = None
    code: str = "R01"
    reason: str | None = None

    _config_section: ClassVar[str] = "returns"
    _mermaid_arrow: ClassVar[str] = "-->>"
    _reverses_direction: ClassVar[bool] = True
    _is_lifecycle_type: ClassVar[bool] = True
    _is_ledgerable: ClassVar[bool] = True
    _payment_mix_field: ClassVar[str | None] = "include_returns"


class ReversalStep(_LifecycleLedgerMixin, _StepBase):
    type: Literal["reversal"]
    payment_order_id: str | None = None
    reason: str | None = None

    _config_section: ClassVar[str] = "reversals"
    _mermaid_arrow: ClassVar[str] = "-->>"
    _reverses_direction: ClassVar[bool] = True
    _is_lifecycle_type: ClassVar[bool] = True
    _is_ledgerable: ClassVar[bool] = True
    _payment_mix_field: ClassVar[str | None] = "include_reversals"


class TransitionLedgerTransactionStep(_StepBase):
    type: Literal["transition_ledger_transaction"]
    ledger_transaction_id: str | None = None
    status: Literal["pending", "posted", "archived"]

    _config_section: ClassVar[str] = "transition_ledger_transactions"
    _mermaid_arrow: ClassVar[str] = "->>"
    _is_lifecycle_type: ClassVar[bool] = True


# ---------------------------------------------------------------------------
# Discriminated union + derived constants
# ---------------------------------------------------------------------------

FundsFlowStep = Annotated[
    Union[
        PaymentOrderStep,
        IncomingPaymentDetailStep,
        ExpectedPaymentStep,
        LedgerTransactionStep,
        ReturnStep,
        ReversalStep,
        TransitionLedgerTransactionStep,
    ],
    Field(discriminator="type"),
]

_STEP_UNION_MEMBERS: tuple[type[_StepBase], ...] = get_args(
    get_args(FundsFlowStep)[0]
)

_STEP_TYPE_MODELS: dict[str, type[_StepBase]] = {
    get_args(m.model_fields["type"].annotation)[0]: m
    for m in _STEP_UNION_MEMBERS
}

VALID_STEP_TYPES: frozenset[str] = frozenset(_STEP_TYPE_MODELS)

RESOURCE_TYPE_TO_SECTION: dict[str, str] = {
    name: model._config_section for name, model in _STEP_TYPE_MODELS.items()
}

ARROW_BY_TYPE: dict[str, str] = {
    name: model._mermaid_arrow for name, model in _STEP_TYPE_MODELS.items()
}

NEEDS_PAYMENT_TYPE: frozenset[str] = frozenset(
    name for name, model in _STEP_TYPE_MODELS.items()
    if "payment_type" in model.model_fields
)

INLINE_LT_TYPES: frozenset[str] = frozenset(
    name for name, model in _STEP_TYPE_MODELS.items()
    if "ledger_inline" in model.model_fields
)

PAYMENT_MIX_TYPE_MAP: dict[str, str] = {
    model._payment_mix_field: name
    for name, model in _STEP_TYPE_MODELS.items()
    if model._payment_mix_field
}

REVERSES_DIRECTION: frozenset[str] = frozenset(
    name for name, model in _STEP_TYPE_MODELS.items()
    if model._reverses_direction
)


def _extract_step_ref(ref: str) -> str | None:
    """Return step_id from a plain reference, or None for $ref: strings."""
    if ref.startswith("$ref:"):
        return None
    return ref


_FundsFlowStepAdapter = TypeAdapter(FundsFlowStep)


class FundsFlowStepConfig:
    """Deprecated factory — use typed step models directly.

    Kept for backward compatibility: both constructor and model_validate()
    delegate to the FundsFlowStep discriminated union and return the
    correctly-typed step model (e.g. PaymentOrderStep).
    """

    def __new__(cls, **kwargs):
        return _FundsFlowStepAdapter.validate_python(kwargs)

    @classmethod
    def model_validate(cls, data):
        return _FundsFlowStepAdapter.validate_python(data)
