"""MT resource configuration models — Layers 0 through 6.

Organized by dependency layer: types with no inter-dependencies first,
types that reference others later.
"""

from __future__ import annotations

import base64 as _b64
import warnings
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.sandbox import (
    SANDBOX_FAILURE_PREFIX,
    SANDBOX_RETURN_PREFIX,
    SANDBOX_ROUTING_NUMBER,
    SANDBOX_SUCCESS_ACCOUNT,
)
from models.shared import (
    AccountDetailConfig,
    AddressConfig,
    DisplayPhase,
    InlineLedgerAccountConfig,
    InlineLedgerEntryConfig,
    InlineLedgerTransactionConfig,
    MetadataMixin,
    RefStr,
    ReversalReason,
    RoutingDetailConfig,
    _BaseResourceConfig,
)

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


class CounterpartyAccountConfig(MetadataMixin, BaseModel):
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

    @model_validator(mode="after")
    def _apply_sandbox_behavior(self) -> CounterpartyAccountConfig:
        if self.sandbox_behavior is None:
            return self

        if self.sandbox_behavior == "success":
            acct_num = SANDBOX_SUCCESS_ACCOUNT
        elif self.sandbox_behavior == "return":
            code = (self.sandbox_return_code or "R01").upper()
            digits = code.lstrip("R")
            acct_num = f"{SANDBOX_RETURN_PREFIX}{digits.zfill(2)}"
        elif self.sandbox_behavior == "failure":
            acct_num = f"{SANDBOX_FAILURE_PREFIX}10"
        else:
            return self

        self.account_details = [AccountDetailConfig(account_number=acct_num)]
        self.routing_details = [
            RoutingDetailConfig(
                routing_number=SANDBOX_ROUTING_NUMBER,
                routing_number_type="aba",
                payment_type="ach",
            ),
            RoutingDetailConfig(
                routing_number=SANDBOX_ROUTING_NUMBER,
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
    email: str | None = None
    send_remittance_advice: bool | None = None
    taxpayer_identifier: str | None = None


class LedgerAccountConfig(MetadataMixin, _BaseResourceConfig):
    display_phase: ClassVar[int] = DisplayPhase.SETUP
    resource_type: ClassVar[str] = "ledger_account"

    name: str
    ledger_id: RefStr
    normal_balance: Literal["credit", "debit"]
    currency: str = "USD"
    description: str | None = None
    ledgerable_type: Literal["external_account", "internal_account", "virtual_account"] | None = None
    ledgerable_id: RefStr | None = None


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
    status: Literal[
        "active", "closed", "pending_closure",
        "pending_activation", "suspended",
    ] | None = None
    counterparty_id: RefStr | None = None
    legal_entity_id: RefStr | None = None
    party_address: AddressConfig | None = None
    parent_account_id: RefStr | None = None
    entity_id: str | None = None


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
    plaid_processor_token: str | None = None
    contact_details: list[dict] | None = None

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
    account_details: list[AccountDetailConfig] | None = None
    routing_details: list[RoutingDetailConfig] | None = None
    credit_ledger_account_id: RefStr | None = None
    debit_ledger_account_id: RefStr | None = None


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
    priority: Literal["high", "normal"] | None = None
    charge_bearer: Literal["OUR", "BEN", "SHA"] | None = None
    receiving_account: dict | None = None
    ultimate_originating_party_name: str | None = None
    ultimate_originating_party_identifier: str | None = None
    ultimate_receiving_party_name: str | None = None
    ultimate_receiving_party_identifier: str | None = None
    remittance_information: str | None = None
    purpose: str | None = None
    fallback_type: str | None = None
    nsf_protected: bool | None = None

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


class IncomingPaymentDetailConfig(MetadataMixin, _BaseResourceConfig):
    """Simulated incoming payment.

    The MT SDK's ``create_async()`` does NOT accept a ``metadata`` parameter,
    but we include ``MetadataMixin`` so the compiler can stamp trace metadata
    uniformly on all resources.  The handler strips ``metadata`` before the
    SDK call.

    All fields here are business-required for demo usability — typed as
    required (no defaults) to catch incomplete configs early.
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
    originating_account_number: str | None = None
    originating_routing_number: str | None = None


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
    posted_at: str | None = None


class ReturnConfig(MetadataMixin, _BaseResourceConfig):
    """Return against an incoming payment detail or a payment order.

    The MT SDK's ``returns.create()`` does NOT accept a ``metadata`` parameter,
    but we include ``MetadataMixin`` so the compiler can stamp trace metadata
    uniformly on all resources.  The handler strips ``metadata`` before the
    SDK call.
    """

    display_phase: ClassVar[int] = DisplayPhase.LIFECYCLE
    resource_type: ClassVar[str] = "return"

    returnable_id: RefStr
    returnable_type: Literal["incoming_payment_detail", "payment_order"] = "incoming_payment_detail"
    code: str = "R01"
    reason: str | None = None
    date_of_death: str | None = None
    ledger_transaction: InlineLedgerTransactionConfig | None = None


# ---------------------------------------------------------------------------
# Layer 6 — Post-create mutations
# ---------------------------------------------------------------------------

class ReversalConfig(MetadataMixin, _BaseResourceConfig):
    display_phase: ClassVar[int] = DisplayPhase.MUTATIONS
    resource_type: ClassVar[str] = "reversal"

    payment_order_id: RefStr
    reason: ReversalReason
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


class TransitionLedgerTransactionConfig(MetadataMixin, _BaseResourceConfig):
    """Transition an existing LT to a new status (pending->posted, posted->archived)."""

    display_phase: ClassVar[int] = DisplayPhase.MUTATIONS
    resource_type: ClassVar[str] = "transition_ledger_transaction"

    ledger_transaction_id: RefStr
    status: Literal["posted", "archived"]


# ---------------------------------------------------------------------------
# New resource types (Feature Audit)
# ---------------------------------------------------------------------------


class LedgerAccountSettlementConfig(MetadataMixin, _BaseResourceConfig):
    """Netting/sweep: zero out pending balances between two ledger accounts."""

    display_phase: ClassVar[int] = DisplayPhase.LIFECYCLE
    resource_type: ClassVar[str] = "ledger_account_settlement"

    settled_ledger_account_id: RefStr
    contra_ledger_account_id: RefStr
    description: str | None = None
    effective_at_upper_bound: str | None = None
    allow_either_direction: bool | None = None
    skip_settlement_ledger_transaction: bool | None = None
    status: Literal["pending", "posted"] | None = None


class BalanceMonitorAlertCondition(BaseModel):
    """Inline alert condition for a balance monitor."""

    model_config = ConfigDict(extra="forbid")

    field: str
    operator: str
    value: int


class LedgerAccountBalanceMonitorConfig(MetadataMixin, _BaseResourceConfig):
    """Alert when a ledger account balance crosses a threshold."""

    display_phase: ClassVar[int] = DisplayPhase.LIFECYCLE
    resource_type: ClassVar[str] = "ledger_account_balance_monitor"

    ledger_account_id: RefStr
    alert_condition: BalanceMonitorAlertCondition
    description: str | None = None


class LedgerAccountStatementConfig(MetadataMixin, _BaseResourceConfig):
    """Point-in-time snapshot of a ledger account's balances and entries."""

    display_phase: ClassVar[int] = DisplayPhase.LIFECYCLE
    resource_type: ClassVar[str] = "ledger_account_statement"

    ledger_account_id: RefStr
    effective_at_lower_bound: str
    effective_at_upper_bound: str
    description: str | None = None


class LegalEntityAssociationConfig(MetadataMixin, _BaseResourceConfig):
    """Associate a child LE as beneficial owner / control person of a parent LE."""

    display_phase: ClassVar[int] = DisplayPhase.SETUP
    resource_type: ClassVar[str] = "legal_entity_association"

    parent_legal_entity_id: RefStr
    child_legal_entity_id: RefStr
    relationship_types: list[
        Literal["beneficial_owner", "control_person"]
    ] | None = None
    title: str | None = None
    ownership_percentage: int | None = None


class TransactionConfig(MetadataMixin, _BaseResourceConfig):
    """Simulated transaction for sandbox reconciliation testing.

    Use sparingly — most transactions should come from IPDs or PO
    settlements.  Direct creation is for edge-case testing only.
    """

    display_phase: ClassVar[int] = DisplayPhase.LIFECYCLE
    resource_type: ClassVar[str] = "transaction"

    internal_account_id: RefStr
    amount: int = Field(..., gt=0)
    direction: Literal["credit", "debit"]
    type: str | None = None
    description: str | None = None
    as_of_date: str | None = None
    posted: bool = True
    vendor_code: str | None = None
    currency: str | None = None
