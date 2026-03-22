"""Pydantic schema layer for the Modern Treasury Dataloader.

Defines every config type, shared type, internal return type, and app setting.
All other modules import from here — no domain types are defined elsewhere.

Organization follows the MT resource dependency graph: types with no
dependencies are defined first; types that reference others come later.
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Annotated, ClassVar, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    # Constants & custom types
    "REF_PATTERN",
    "RESOURCE_TYPES",
    "RefStr",
    "DisplayPhase",
    # Layer 0 — connections (sandbox-only creation)
    "ConnectionConfig",
    # Shared types
    "MetadataMixin",
    "AddressConfig",
    "AccountDetailConfig",
    "RoutingDetailConfig",
    "InlineLedgerAccountConfig",
    # Layer 1 — foundation
    "LegalEntityConfig",
    "LedgerConfig",
    # Layer 2 — depends on layer 1
    "CounterpartyAccountConfig",
    "CounterpartyConfig",
    "LedgerAccountConfig",
    # Layer 3 — depends on layers 1-2
    "InternalAccountConfig",
    "ExternalAccountConfig",
    "LedgerAccountCategoryConfig",
    # Layer 4 — depends on layer 3
    "VirtualAccountConfig",
    "ReconciliationRuleVariable",
    "ExpectedPaymentConfig",
    "InlineLedgerEntryConfig",
    "InlineLedgerTransactionConfig",
    "LineItemConfig",
    "PaymentOrderConfig",
    # Layer 5 — lifecycle / simulation
    "IncomingPaymentDetailConfig",
    "LedgerTransactionConfig",
    "ReturnConfig",
    # Layer 6 — post-create mutations
    "ReversalConfig",
    "CategoryMembershipConfig",
    "NestedCategoryConfig",
    # Top-level config
    "DataLoaderConfig",
    # Internal types
    "HandlerResult",
    "ManifestEntry",
    "FailedEntry",
    "StagedEntry",
    # App settings
    "AppSettings",
]

# ---------------------------------------------------------------------------
# Constants & custom types
# ---------------------------------------------------------------------------

REF_PATTERN = re.compile(
    r"^\$ref:[a-z_]+\.[a-zA-Z0-9_]+(\.[a-zA-Z0-9_\[\]]+)*$"
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


class InlineLedgerAccountConfig(BaseModel):
    """Ledger account created inline on an external/virtual/internal account.

    Distinct from ``LedgerAccountConfig``: no ``ref`` (the handler registers
    the auto-created account), no ``ledger_id`` (inferred from context by MT).
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    normal_balance: Literal["credit", "debit"]
    currency: str = "USD"
    description: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Layer 0 — Connections (sandbox-only creation via POST /connections)
# ---------------------------------------------------------------------------


class ConnectionConfig(_BaseResourceConfig):
    """Sandbox connection creation. NO MetadataMixin — the POST /connections
    endpoint only accepts ``entity_id`` and ``nickname``."""

    display_phase: ClassVar[int] = DisplayPhase.SETUP
    resource_type: ClassVar[str] = "connection"

    entity_id: Literal["example1", "example2", "modern_treasury"]
    nickname: str | None = None


# ---------------------------------------------------------------------------
# Layer 1 — Foundation resources (no inter-dependencies)
# ---------------------------------------------------------------------------


class DocumentConfig(BaseModel):
    """Inline document attached to a legal entity or identification."""

    model_config = ConfigDict(extra="forbid")

    document_type: Literal[
        "articles_of_incorporation",
        "certificate_of_good_standing",
        "ein_letter",
        "identification_back",
        "identification_front",
        "proof_of_address",
    ]
    file_data: str
    filename: str | None = None

    @model_validator(mode="after")
    def _default_filename(self) -> DocumentConfig:
        """Ensure filename is always present -- the MT API requires it."""
        if self.filename is None:
            self.filename = f"{self.document_type}.pdf"
        return self


class IdentificationConfig(BaseModel):
    """Legal entity identification (EIN, SSN, passport, etc.)."""

    model_config = ConfigDict(extra="forbid")

    id_number: str
    id_type: Literal[
        "ar_cuil", "ar_cuit", "br_cnpj", "br_cpf", "ca_sin", "cl_run",
        "cl_rut", "co_cedulas", "co_nit", "drivers_license", "hn_id",
        "hn_rtn", "in_lei", "kr_brn", "kr_crn", "kr_rrn", "passport",
        "sa_tin", "sa_vat", "us_ein", "us_itin", "us_ssn", "vn_tin",
    ]
    issuing_country: str | None = None
    documents: list[DocumentConfig] | None = None


import base64 as _b64

_MOCK_PDF_B64: str = _b64.standard_b64encode(
    b"%PDF-1.0\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
    b"xref\n0 4\n"
    b"0000000000 65535 f \n"
    b"0000000009 00000 n \n"
    b"0000000058 00000 n \n"
    b"0000000108 00000 n \n"
    b"trailer<</Root 1 0 R/Size 4>>\n"
    b"startxref\n178\n%%EOF"
).decode()


def _mock_nine_digits(seed: str, offset: int = 0) -> str:
    """Deterministic 9-digit number from a seed string. Never starts with 0."""
    h = hash(seed) + offset
    n = abs(h) % 900_000_000 + 100_000_000
    return str(n)


class PhoneNumberConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone_number: str


class WealthAndEmploymentDetailsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    annual_income: int | None = None
    source_of_funds: Literal[
        "alimony", "annuity", "business_owner", "business_revenue",
        "debt_financing", "general_employee", "government_benefits",
        "homemaker", "inheritance_gift", "intercompany_loan", "investment",
        "investor_funding", "legal_settlement", "lottery", "real_estate",
        "retained_earnings_or_savings", "retired", "retirement", "salary",
        "sale_of_business_assets", "sale_of_real_estate", "self_employed",
        "senior_executive", "trust_income",
    ] | None = None
    wealth_source: Literal[
        "business_sale", "family_support", "government_benefits",
        "inheritance", "investments", "other", "rental_income",
        "retirement", "salary", "self_employed",
    ] | None = None
    occupation: Literal[
        "consulting", "executive", "finance_accounting", "food_services",
        "government", "healthcare", "legal_services", "manufacturing",
        "other", "sales", "science_engineering", "technology",
    ] | None = None
    employment_status: Literal[
        "employed", "retired", "self_employed", "student", "unemployed",
    ] | None = None
    employer_name: str | None = None
    employer_country: str | None = None
    income_source: Literal[
        "family_support", "government_benefits", "inheritance",
        "investments", "rental_income", "retirement", "salary",
        "self_employed",
    ] | None = None
    industry: Literal[
        "accounting", "agriculture", "automotive",
        "chemical_manufacturing", "construction", "educational_medical",
        "food_service", "finance", "gasoline", "health_stores", "laundry",
        "maintenance", "manufacturing", "merchant_wholesale", "mining",
        "performing_arts", "professional_non_legal", "public_administration",
        "publishing", "real_estate", "recreation_gambling",
        "religious_charity", "rental_services", "retail_clothing",
        "retail_electronics", "retail_food", "retail_furnishing",
        "retail_home", "retail_non_store", "retail_sporting",
        "transportation", "travel", "utilities",
    ] | None = None


class LegalEntityConfig(MetadataMixin, _BaseResourceConfig):
    """Legal entity config with **automatic mock data** for sandbox demos.

    For a business, provide only ``legal_entity_type`` and ``business_name``.
    For an individual, provide ``legal_entity_type``, ``first_name``, and
    ``last_name``.  The model validator fills in all remaining KYB/KYC fields
    (address, identifications, dates, documents, phone, wealth details) with
    compliant mock values so the MT sandbox accepts them.

    Any field you *do* set explicitly is kept as-is (except compliance
    fields which are always overwritten).
    """

    display_phase: ClassVar[int] = DisplayPhase.SETUP
    resource_type: ClassVar[str] = "legal_entity"

    legal_entity_type: Literal["business", "individual"]

    # Individual fields
    first_name: str | None = None
    last_name: str | None = None
    middle_name: str | None = None
    date_of_birth: str | None = None
    citizenship_country: str | None = None
    email: str | None = None

    # Business fields
    business_name: str | None = None
    date_formed: str | None = None
    legal_structure: Literal[
        "corporation", "llc", "non_profit", "partnership",
        "sole_proprietorship", "trust",
    ] | None = None
    country_of_incorporation: str | None = None
    website: str | None = None

    # Shared
    addresses: list[AddressConfig] | None = None
    identifications: list[IdentificationConfig] | None = None
    documents: list[DocumentConfig] | None = None
    phone_numbers: list[PhoneNumberConfig] | None = None
    operating_jurisdictions: list[str] | None = None
    intended_use: str | None = None
    expected_activity_volume: int | None = None
    wealth_and_employment_details: WealthAndEmploymentDetailsConfig | None = None

    @model_validator(mode="after")
    def _fill_mock_compliance_data(self) -> LegalEntityConfig:
        """Always overwrite compliance fields with sandbox-safe mock data.

        Identifications, addresses, documents, phone numbers, and
        wealth/employment details are fully managed by the mock -- any
        values the JSON provides are silently replaced.

        Documents live at two levels:
        - Entity-level ``documents`` (articles_of_incorporation, proof_of_address)
        - Nested on ``identifications[].documents`` (ein_letter, identification_front)
        """
        seed = self.ref

        if self.phone_numbers is None:
            self.phone_numbers = [PhoneNumberConfig(phone_number="+15551234567")]
        if self.intended_use is None:
            self.intended_use = "Sandbox demo and testing"
        if self.expected_activity_volume is None:
            self.expected_activity_volume = 100

        if self.legal_entity_type == "business":
            if self.date_formed is None:
                self.date_formed = "2020-01-15"
            if self.legal_structure is None:
                self.legal_structure = "llc"
            if self.country_of_incorporation is None:
                self.country_of_incorporation = "US"
            if self.email is None:
                self.email = "compliance@example.com"
            if self.operating_jurisdictions is None:
                self.operating_jurisdictions = ["US"]
            if self.wealth_and_employment_details is None:
                self.wealth_and_employment_details = (
                    WealthAndEmploymentDetailsConfig(
                        source_of_funds="business_revenue",
                        industry="finance",
                    )
                )
            self.identifications = [
                IdentificationConfig(
                    id_number=_mock_nine_digits(seed),
                    id_type="us_ein",
                    documents=[
                        DocumentConfig(
                            document_type="ein_letter",
                            file_data=_MOCK_PDF_B64,
                        ),
                    ],
                )
            ]
            self.addresses = [
                AddressConfig(
                    address_types=["business"],
                    line1="100 Main Street",
                    locality="New York",
                    region="NY",
                    postal_code="10001",
                    country="US",
                )
            ]
            self.documents = [
                DocumentConfig(
                    document_type="articles_of_incorporation",
                    file_data=_MOCK_PDF_B64,
                ),
                DocumentConfig(
                    document_type="proof_of_address",
                    file_data=_MOCK_PDF_B64,
                ),
            ]

        elif self.legal_entity_type == "individual":
            if self.date_of_birth is None:
                self.date_of_birth = "1990-06-15"
            if self.citizenship_country is None:
                self.citizenship_country = "US"
            if self.email is None:
                self.email = "individual@example.com"
            if self.middle_name is None:
                self.middle_name = "M"
            if self.wealth_and_employment_details is None:
                self.wealth_and_employment_details = (
                    WealthAndEmploymentDetailsConfig(
                        annual_income=100000,
                        wealth_source="salary",
                        occupation="technology",
                        employment_status="employed",
                        income_source="salary",
                        source_of_funds="salary",
                    )
                )
            self.identifications = [
                IdentificationConfig(
                    id_number=_mock_nine_digits(seed, offset=1),
                    id_type="us_ssn",
                ),
                IdentificationConfig(
                    id_number="A" + _mock_nine_digits(seed, offset=2)[:8],
                    id_type="passport",
                    issuing_country="US",
                    documents=[
                        DocumentConfig(
                            document_type="identification_front",
                            file_data=_MOCK_PDF_B64,
                        ),
                    ],
                ),
            ]
            self.addresses = [
                AddressConfig(
                    address_types=["residential"],
                    line1="200 Oak Avenue",
                    locality="Austin",
                    region="TX",
                    postal_code="73301",
                    country="US",
                )
            ]
            self.documents = None

        return self


class LedgerConfig(MetadataMixin, _BaseResourceConfig):
    display_phase: ClassVar[int] = DisplayPhase.SETUP
    resource_type: ClassVar[str] = "ledger"

    name: str
    description: str | None = None


# ---------------------------------------------------------------------------
# Layer 2 — Depends on layer 1
# ---------------------------------------------------------------------------


class CounterpartyAccountConfig(BaseModel):
    """Inline external account created with the counterparty.

    Separate from ``ExternalAccountConfig`` because the SDK shape differs:
    no ``counterparty_id`` (implicit), distinct TypedDict for account/routing
    details.

    Sandbox behavior (optional):
        Set ``sandbox_behavior`` to auto-generate the magic account number
        that the MT sandbox uses to simulate payment outcomes.

        * ``"success"``  → account ``123456789`` (POs succeed)
        * ``"return"``   → account ``100XX`` where XX comes from
          ``sandbox_return_code`` (default ``"R01"``).  POs sent here
          auto-generate a return with the specified ACH code.
        * ``"failure"``  → account ``1111111110`` (POs fail outright)

        When set, ``account_details`` and ``routing_details`` are
        auto-populated (ABA 121141822).  Any explicit values are
        overwritten.  Both fields are excluded from ``model_dump()``
        so they never reach the MT API.

    See https://docs.moderntreasury.com/payments/docs/test-counterparties
    """

    model_config = ConfigDict(extra="forbid")

    sandbox_behavior: Literal["success", "failure", "return"] | None = Field(
        None, exclude=True,
    )
    sandbox_return_code: str | None = Field(
        None, exclude=True,
    )

    account_type: str | None = None
    party_name: str | None = None
    party_type: Literal["business", "individual"] | None = None
    party_address: AddressConfig | None = None
    account_details: list[AccountDetailConfig] = []
    routing_details: list[RoutingDetailConfig] = []
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _apply_sandbox_behavior(self) -> CounterpartyAccountConfig:
        if self.sandbox_behavior is None:
            return self

        if self.sandbox_behavior == "success":
            acct_num = "123456789"
        elif self.sandbox_behavior == "return":
            code = (self.sandbox_return_code or "R01").upper()
            digits = code.lstrip("R")
            acct_num = f"100{digits.zfill(2)}"
        elif self.sandbox_behavior == "failure":
            acct_num = "1111111110"
        else:
            return self

        self.account_details = [AccountDetailConfig(account_number=acct_num)]
        self.routing_details = [
            RoutingDetailConfig(
                routing_number="121141822",
                routing_number_type="aba",
                payment_type="ach",
            ),
            RoutingDetailConfig(
                routing_number="121141822",
                routing_number_type="aba",
                payment_type="wire",
            ),
        ]
        return self


class CounterpartyConfig(MetadataMixin, _BaseResourceConfig):
    display_phase: ClassVar[int] = DisplayPhase.SETUP
    resource_type: ClassVar[str] = "counterparty"

    name: str
    legal_entity_id: RefStr | None = None
    accounts: list[CounterpartyAccountConfig] | None = None


class LedgerAccountConfig(MetadataMixin, _BaseResourceConfig):
    display_phase: ClassVar[int] = DisplayPhase.SETUP
    resource_type: ClassVar[str] = "ledger_account"

    name: str
    ledger_id: RefStr
    normal_balance: Literal["credit", "debit"]
    currency: str = "USD"
    description: str | None = None


# ---------------------------------------------------------------------------
# Layer 3 — Depends on layers 1-2
# ---------------------------------------------------------------------------


class InternalAccountConfig(MetadataMixin, _BaseResourceConfig):
    display_phase: ClassVar[int] = DisplayPhase.SETUP
    resource_type: ClassVar[str] = "internal_account"

    connection_id: RefStr
    name: str
    party_name: str
    currency: Literal["USD", "CAD", "USDC", "USDG"]
    counterparty_id: RefStr | None = None
    legal_entity_id: RefStr | None = None
    party_address: AddressConfig | None = None


class ExternalAccountConfig(MetadataMixin, _BaseResourceConfig):
    display_phase: ClassVar[int] = DisplayPhase.BUSINESS
    resource_type: ClassVar[str] = "external_account"

    counterparty_id: RefStr
    account_details: list[AccountDetailConfig] = []
    routing_details: list[RoutingDetailConfig] = []
    account_type: str | None = None
    party_name: str | None = None
    party_type: Literal["business", "individual"] | None = None
    party_address: AddressConfig | None = None
    ledger_account: InlineLedgerAccountConfig | None = None

    @model_validator(mode="after")
    def _warn_missing_account_details(self) -> ExternalAccountConfig:
        if not self.account_details or not self.routing_details:
            warnings.warn(
                f"External account '{self.ref}' has no account_details or "
                f"routing_details. It may be unusable for payment orders "
                f"unless these are provided by a parent counterparty's "
                f"inline accounts[].",
                UserWarning,
                stacklevel=2,
            )
        return self


class LedgerAccountCategoryConfig(MetadataMixin, _BaseResourceConfig):
    display_phase: ClassVar[int] = DisplayPhase.SETUP
    resource_type: ClassVar[str] = "ledger_account_category"

    name: str
    ledger_id: RefStr
    normal_balance: Literal["credit", "debit"]
    currency: str = "USD"
    description: str | None = None


# ---------------------------------------------------------------------------
# Layer 4 — Depends on layer 3
# ---------------------------------------------------------------------------


class VirtualAccountConfig(MetadataMixin, _BaseResourceConfig):
    display_phase: ClassVar[int] = DisplayPhase.BUSINESS
    resource_type: ClassVar[str] = "virtual_account"

    name: str
    internal_account_id: RefStr
    counterparty_id: RefStr | None = None
    description: str | None = None
    ledger_account: InlineLedgerAccountConfig | None = None


class ReconciliationRuleVariable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    internal_account_id: RefStr
    direction: Literal["credit", "debit"]
    amount_lower_bound: int = Field(..., ge=0)
    amount_upper_bound: int = Field(..., ge=0)
    currency: str | None = None
    type: str | None = None
    date_lower_bound: str | None = None
    date_upper_bound: str | None = None
    counterparty_id: RefStr | None = None
    custom_identifiers: dict[str, str] | None = None

    @model_validator(mode="after")
    def _bounds_are_ordered(self) -> ReconciliationRuleVariable:
        if self.amount_lower_bound > self.amount_upper_bound:
            raise ValueError(
                f"amount_lower_bound ({self.amount_lower_bound}) must be "
                f"<= amount_upper_bound ({self.amount_upper_bound})"
            )
        return self


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


class ExpectedPaymentConfig(MetadataMixin, _BaseResourceConfig):
    display_phase: ClassVar[int] = DisplayPhase.BUSINESS
    resource_type: ClassVar[str] = "expected_payment"

    description: str | None = None
    amount_upper_bound: int | None = Field(None, ge=0)
    amount_lower_bound: int | None = Field(None, ge=0)
    direction: Literal["credit", "debit"] | None = None
    internal_account_id: RefStr | None = None
    type: str | None = None
    statement_descriptor: str | None = None
    date_lower_bound: str | None = None
    date_upper_bound: str | None = None
    counterparty_id: RefStr | None = None
    currency: str | None = None
    reconciliation_filters: dict | None = None
    reconciliation_groups: dict | None = None
    reconciliation_rule_variables: list[ReconciliationRuleVariable] | None = None
    ledger_transaction: InlineLedgerTransactionConfig | None = None
    staged: bool = Field(default=False, exclude=True)

    @model_validator(mode="after")
    def _business_level_required_fields(self) -> ExpectedPaymentConfig:
        """The MT API requires nothing, but a demo EP is useless without
        reconciliation rule variables."""
        if not self.reconciliation_rule_variables:
            raise ValueError(
                f"Expected Payment '{self.ref}' is missing "
                f"reconciliation_rule_variables. The MT API allows this "
                f"but it renders the EP unusable for demos."
            )
        return self


class LineItemConfig(MetadataMixin, BaseModel):
    """Payment order line item — supports its own metadata."""

    model_config = ConfigDict(extra="forbid")

    amount: int = Field(..., gt=0)
    description: str | None = None


class PaymentOrderConfig(MetadataMixin, _BaseResourceConfig):
    display_phase: ClassVar[int] = DisplayPhase.BUSINESS
    resource_type: ClassVar[str] = "payment_order"

    type: str
    subtype: Literal[
        "CCD", "PPD", "IAT", "CTX", "WEB", "CIE", "TEL",
    ] | None = None
    amount: int = Field(..., gt=0)
    direction: Literal["credit", "debit"]
    originating_account_id: RefStr
    receiving_account_id: RefStr | None = None
    currency: str | None = None
    description: str | None = None
    statement_descriptor: str | None = None
    effective_date: str | None = None
    ledger_transaction: InlineLedgerTransactionConfig | None = None
    line_items: list[LineItemConfig] | None = None
    staged: bool = Field(default=False, exclude=True)

    @model_validator(mode="after")
    def _credit_needs_receiver(self) -> PaymentOrderConfig:
        if self.direction == "credit" and not self.receiving_account_id:
            raise ValueError(
                f"Payment order '{self.ref}' has direction='credit' but no "
                f"receiving_account_id. Credit POs require a receiving account."
            )
        return self


# ---------------------------------------------------------------------------
# Layer 5 — Lifecycle / simulation
# ---------------------------------------------------------------------------


class IncomingPaymentDetailConfig(_BaseResourceConfig):
    """Simulated incoming payment.  Does NOT support metadata (no MetadataMixin).

    The SDK has zero required params for ``create_async()``.  All fields here
    are business-required for demo usability — typed as required (no defaults)
    to catch incomplete configs early.
    """

    display_phase: ClassVar[int] = DisplayPhase.LIFECYCLE
    resource_type: ClassVar[str] = "incoming_payment_detail"

    type: str
    direction: Literal["credit", "debit"]
    amount: int = Field(..., gt=0)
    internal_account_id: RefStr
    currency: str | None = None
    virtual_account_id: RefStr | None = None
    as_of_date: str | None = None
    description: str | None = None
    staged: bool = Field(default=False, exclude=True)


class LedgerTransactionConfig(MetadataMixin, _BaseResourceConfig):
    """Standalone ledger transaction (not inline on a PO)."""

    display_phase: ClassVar[int] = DisplayPhase.LIFECYCLE
    resource_type: ClassVar[str] = "ledger_transaction"

    ledger_entries: list[InlineLedgerEntryConfig] = Field(..., min_length=1)
    description: str | None = None
    effective_at: str | None = None
    effective_date: str | None = None
    external_id: str | None = None
    status: Literal["archived", "pending", "posted"] | None = None
    ledgerable_type: str | None = None
    ledgerable_id: RefStr | None = None
    staged: bool = Field(default=False, exclude=True)


class ReturnConfig(_BaseResourceConfig):
    """Return against an incoming payment detail.  Does NOT support metadata.

    ``returnable_type`` is a ClassVar — the only valid value is
    ``"incoming_payment_detail"`` so there is no reason to make the user
    specify it.
    """

    display_phase: ClassVar[int] = DisplayPhase.LIFECYCLE
    resource_type: ClassVar[str] = "return"
    returnable_type: ClassVar[str] = "incoming_payment_detail"

    returnable_id: RefStr
    code: str | None = None
    reason: str | None = None
    date_of_death: str | None = None
    ledger_transaction: InlineLedgerTransactionConfig | None = None


# ---------------------------------------------------------------------------
# Layer 6 — Post-create mutations
# ---------------------------------------------------------------------------

REVERSAL_REASONS = Literal[
    "duplicate",
    "incorrect_amount",
    "incorrect_receiving_account",
    "date_earlier_than_intended",
    "date_later_than_intended",
]


class ReversalConfig(MetadataMixin, _BaseResourceConfig):
    display_phase: ClassVar[int] = DisplayPhase.MUTATIONS
    resource_type: ClassVar[str] = "reversal"

    payment_order_id: RefStr
    reason: REVERSAL_REASONS
    ledger_transaction: InlineLedgerTransactionConfig | None = None


class CategoryMembershipConfig(_BaseResourceConfig):
    """Add a ledger account to a ledger account category.  No metadata."""

    display_phase: ClassVar[int] = DisplayPhase.MUTATIONS
    resource_type: ClassVar[str] = "category_membership"

    category_id: RefStr
    ledger_account_id: RefStr


class NestedCategoryConfig(_BaseResourceConfig):
    """Add a sub-category to a parent ledger account category.  No metadata."""

    display_phase: ClassVar[int] = DisplayPhase.MUTATIONS
    resource_type: ClassVar[str] = "nested_category"

    parent_category_id: RefStr
    sub_category_id: RefStr


# ---------------------------------------------------------------------------
# Top-level config
# ---------------------------------------------------------------------------


class DataLoaderConfig(BaseModel):
    """Top-level dataloader configuration parsed from the user's JSON file.

    Sections default to empty lists so the user only includes what they need.
    ``extra='forbid'`` catches typos in section names immediately.
    """

    model_config = ConfigDict(extra="forbid")

    # Layer 0 — connections (sandbox-only)
    connections: list[ConnectionConfig] = []

    # Layer 1
    legal_entities: list[LegalEntityConfig] = []
    ledgers: list[LedgerConfig] = []

    # Layer 2
    counterparties: list[CounterpartyConfig] = []
    ledger_accounts: list[LedgerAccountConfig] = []

    # Layer 3
    internal_accounts: list[InternalAccountConfig] = []
    external_accounts: list[ExternalAccountConfig] = []
    ledger_account_categories: list[LedgerAccountCategoryConfig] = []

    # Layer 4
    virtual_accounts: list[VirtualAccountConfig] = []
    expected_payments: list[ExpectedPaymentConfig] = []
    payment_orders: list[PaymentOrderConfig] = []

    # Layer 5
    incoming_payment_details: list[IncomingPaymentDetailConfig] = []
    ledger_transactions: list[LedgerTransactionConfig] = []
    returns: list[ReturnConfig] = []

    # Layer 6
    reversals: list[ReversalConfig] = []
    category_memberships: list[CategoryMembershipConfig] = []
    nested_categories: list[NestedCategoryConfig] = []

    @model_validator(mode="after")
    def _refs_are_unique_within_type(self) -> DataLoaderConfig:
        """Catch duplicate refs before the engine even sees them."""
        seen: dict[str, str] = {}
        for section_name in self.model_fields:
            items = getattr(self, section_name)
            for item in items:
                if not hasattr(item, "resource_type"):
                    continue
                typed_ref = f"{item.resource_type}.{item.ref}"
                if typed_ref in seen:
                    raise ValueError(
                        f"Duplicate ref '{typed_ref}' in sections "
                        f"'{seen[typed_ref]}' and '{section_name}'"
                    )
                seen[typed_ref] = section_name
        return self


# ---------------------------------------------------------------------------
# Internal types (not user-facing — handler returns & manifest entries)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HandlerResult:
    """Returned by every handler after a successful SDK create call."""

    created_id: str
    resource_type: str
    typed_ref: str = ""
    child_refs: dict[str, str] = field(default_factory=dict)
    raw_response: dict | None = None
    deletable: bool = True


@dataclass(frozen=True)
class ManifestEntry:
    """Single resource entry in a run manifest."""

    batch: int
    resource_type: str
    typed_ref: str
    created_id: str
    created_at: str
    deletable: bool
    child_refs: dict[str, str] = field(default_factory=dict)
    cleanup_status: str | None = None


@dataclass(frozen=True)
class FailedEntry:
    """Single failed resource entry in a run manifest."""

    typed_ref: str
    error: str
    failed_at: str


@dataclass(frozen=True)
class StagedEntry:
    """Resource resolved but not sent to API — staged for manual fire during demo."""

    resource_type: str
    typed_ref: str
    staged_at: str


# ---------------------------------------------------------------------------
# App settings
# ---------------------------------------------------------------------------


class AppSettings(BaseSettings):
    """Application configuration loaded from env vars / ``.env`` file.

    All variables are prefixed with ``DATALOADER_`` (e.g.
    ``DATALOADER_MT_API_KEY``).  The API key and org ID can also be supplied
    per-request from the UI form, overriding env defaults.
    """

    model_config = SettingsConfigDict(
        env_prefix="DATALOADER_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    mt_api_key: str = ""
    mt_org_id: str = ""
    baseline_path: str = "baseline.yaml"
    runs_dir: str = "runs"
    log_level: str = "INFO"
    stamp_loader_metadata: bool = False
    max_concurrent_requests: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Max concurrent MT API calls within a batch",
    )
    webhook_secret: str = ""
