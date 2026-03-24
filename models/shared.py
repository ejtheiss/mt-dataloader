"""Shared types, constants, and base classes used across all model sub-modules.

Dependency order: this module has NO intra-package imports — every other
sub-module may import from here without risk of circular references.
"""

from __future__ import annotations

import re
from enum import IntEnum
from typing import Annotated, ClassVar, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

# ---------------------------------------------------------------------------
# Constants & custom types
# ---------------------------------------------------------------------------

REF_PATTERN = re.compile(
    r"^\$ref:[a-z_]+\.[a-zA-Z0-9_{}\[\]]+(\.[a-zA-Z0-9_{}\[\]]+)*$"
)

RESOURCE_TYPES: frozenset[str] = frozenset(
    {
        "connection",
        "legal_entity",
        "ledger",
        "counterparty",
        "ledger_account",
        "internal_account",
        "external_account",
        "ledger_account_category",
        "virtual_account",
        "expected_payment",
        "payment_order",
        "incoming_payment_detail",
        "transaction",
        "ledger_transaction",
        "return",
        "reversal",
        "category_membership",
        "nested_category",
        "transition_ledger_transaction",
    }
)


class DisplayPhase(IntEnum):
    """UI-only grouping for the preview screen. The executor ignores this."""

    SETUP = 1
    BUSINESS = 2
    LIFECYCLE = 3
    MUTATIONS = 4


def _validate_ref_or_literal(v: str) -> str:
    """Accept either a ``$ref:type.key[.selector]`` string or a literal UUID."""
    if v.startswith("$ref:"):
        if not REF_PATTERN.match(v):
            raise ValueError(
                f"Invalid ref format: '{v}'. "
                f"Expected $ref:<type>.<key>[.<selector>]"
            )
        ref_type = v.split(":")[1].split(".")[0]
        if ref_type not in RESOURCE_TYPES:
            raise ValueError(
                f"Unknown resource type '{ref_type}' in ref '{v}'. "
                f"Valid types: {sorted(RESOURCE_TYPES)}"
            )
    return v


RefStr = Annotated[str, AfterValidator(_validate_ref_or_literal)]
"""A string that is either a literal UUID or a typed symbolic ref
(``$ref:type.key[.selector]``).  Format-validated at parse time; resolution
to an actual UUID happens in the engine layer."""

# ---------------------------------------------------------------------------
# Shared / reusable types
# ---------------------------------------------------------------------------


class MetadataMixin(BaseModel):
    """Mixin for resources whose MT API accepts ``metadata``.

    Metadata is business/demo data (ERP IDs, tenant IDs, invoice refs, etc.),
    NOT loader bookkeeping.  Passed through to the SDK unchanged.
    """

    metadata: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Business metadata passed through to Modern Treasury. "
            "Keys and values must be strings."
        ),
    )


class _BaseResourceConfig(BaseModel):
    """Private base for all resource configs.  Provides the ``ref`` field,
    its validator, and ``extra='forbid'`` so typos in config keys are caught
    immediately."""

    model_config = ConfigDict(extra="forbid")

    ref: str = Field(
        ...,
        min_length=1,
        description="Unique key for this resource within its type",
    )

    depends_on: list[RefStr] = Field(
        default_factory=list,
        exclude=True,
        description=(
            "Explicit ordering dependencies. Use when a resource must wait "
            "for another that it does NOT reference in a data field. "
            "Example: a book transfer that must wait for an IPD to settle "
            "before moving the deposited funds. Excluded from API payloads."
        ),
    )

    @field_validator("ref")
    @classmethod
    def _ref_must_be_simple_key(cls, v: str) -> str:
        v = v.strip()
        if "." in v or v.startswith("$ref:"):
            raise ValueError(
                f"ref must be a simple key (no dots, no $ref: prefix), got '{v}'. "
                f"The engine auto-prefixes the resource type."
            )
        return v


class AddressConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address_types: list[
        Literal["business", "mailing", "other", "po_box", "residential"]
    ] = Field(
        default_factory=lambda: ["business"],
        description=(
            "Each entry must be one of: business, mailing, other, po_box, residential. "
            "Do not use 'registered' or 'headquarters' — use business for a company's "
            "registered office / HQ, residential for an individual's home."
        ),
    )
    line1: str
    line2: str | None = None
    locality: str
    region: str
    postal_code: str
    country: str = "US"


class AccountDetailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_number: str
    account_number_type: str | None = None


class RoutingDetailConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routing_number: str
    routing_number_type: Literal[
        "aba",
        "au_bsb",
        "br_codigo",
        "ca_cpa",
        "chips",
        "cnaps",
        "dk_interbank_clearing_code",
        "gb_sort_code",
        "hk_interbank_clearing_code",
        "hu_interbank_clearing_code",
        "id_sknbi_code",
        "il_bank_code",
        "in_ifsc",
        "jp_zengin_code",
        "my_branch_code",
        "mx_bank_identifier",
        "nz_national_clearing_code",
        "pl_national_clearing_code",
        "se_bankgiro_clearing_code",
        "sg_interbank_clearing_code",
        "swift",
        "za_national_clearing_code",
    ] = "aba"
    payment_type: Literal[
        "ach",
        "au_becs",
        "bacs",
        "book",
        "card",
        "check",
        "cross_border",
        "eft",
        "interac",
        "neft",
        "nics",
        "nz_becs",
        "provxchange",
        "rtp",
        "sen",
        "sepa",
        "sic",
        "signet",
        "wire",
        "zengin",
    ] | None = None


class InlineLedgerAccountConfig(MetadataMixin, BaseModel):
    """Ledger account created inline on an external/virtual/internal account.

    Distinct from ``LedgerAccountConfig``: no ``ref`` (the handler registers
    the auto-created account), no ``ledger_id`` (inferred from context by MT).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    normal_balance: Literal["credit", "debit"]
    currency: str = "USD"
    description: str | None = None


class InlineLedgerEntryConfig(MetadataMixin, BaseModel):
    """Single debit or credit leg within an inline ledger transaction."""

    model_config = ConfigDict(extra="forbid")

    amount: int = Field(..., gt=0)
    direction: Literal["credit", "debit"]
    ledger_account_id: RefStr


class InlineLedgerTransactionConfig(MetadataMixin, BaseModel):
    """Inline ledger transaction attached to a payment order, return,
    reversal, or expected payment.  Shared type — the SDK uses the same
    ``LedgerTransactionCreateRequest`` shape everywhere."""

    model_config = ConfigDict(extra="forbid")

    ledger_entries: list[InlineLedgerEntryConfig] = Field(..., min_length=1)
    description: str | None = None
    effective_at: str | None = None
    effective_date: str | None = None
    external_id: str | None = None
    status: Literal["archived", "pending", "posted"] | None = None


# ---------------------------------------------------------------------------
# Reusable Annotated types — eliminate repeated Field() boilerplate
# ---------------------------------------------------------------------------

AmountCents = Annotated[int, Field(gt=0, description="Amount in cents")]
CurrencyCode = Annotated[
    str | None,
    Field(default=None, max_length=4, description="ISO 4217 currency code"),
]
PaymentDirection = Literal["credit", "debit"]
LedgerStatus = Literal["pending", "posted"]


# ---------------------------------------------------------------------------
# Timing configuration (Step 9: Seasoning & Date Configuration)
# ---------------------------------------------------------------------------


class StepTimingConfig(BaseModel):
    """Timing for a single step relative to its dependencies."""

    model_config = ConfigDict(extra="forbid")

    delay_days: float = 0.0
    delay_jitter_days: float = 0.0
    business_days_only: bool = True
