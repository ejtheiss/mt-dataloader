"""MT resource models — layers 2–5 (depends on layer 1)."""

from __future__ import annotations

import warnings
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.sandbox import (
    SANDBOX_FAILURE_PREFIX,
    SANDBOX_RETURN_PREFIX,
    SANDBOX_ROUTING_NUMBER,
    SANDBOX_SUCCESS_ACCOUNT,
    SANDBOX_WALLET_DEMO_ADDRESSES,
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
    RoutingDetailConfig,
    WalletAccountNumberType,
    _BaseResourceConfig,
    implied_ledger_currency_exponent,
)

# ---------------------------------------------------------------------------
# Layer 2 — Depends on layer 1
# ---------------------------------------------------------------------------


class CounterpartyAccountConfig(MetadataMixin, BaseModel):
    """Inline external account created with the counterparty.

    Separate from ``ExternalAccountConfig`` because the SDK shape differs:
    no ``counterparty_id`` (implicit), distinct TypedDict for account/routing
    details.

    **Bank / ACH sandbox (optional):**
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

    **Stablecoin wallet counterparties:**
        Use ``wallet_account_number_type`` (mutually exclusive with
        ``sandbox_behavior``) to emit MT's wallet shape: a single
        ``account_details`` entry with ``account_number`` and
        ``account_number_type`` (e.g. ``ethereum_address``). No ABA
        ``routing_details``. For a custom on-chain address, omit both
        helpers and set ``account_details`` explicitly.

    See https://docs.moderntreasury.com/payments/docs/test-counterparties
    """

    model_config = ConfigDict(extra="forbid")

    sandbox_behavior: Literal["success", "failure", "return"] | None = Field(
        None,
        exclude=True,
    )
    sandbox_return_code: str | None = Field(
        None,
        exclude=True,
    )
    wallet_account_number_type: WalletAccountNumberType | None = Field(
        None,
        exclude=True,
        description=(
            "Stablecoin external wallet: fills account_details with a demo address "
            "and the given MT account_number_type. Incompatible with sandbox_behavior."
        ),
    )

    account_type: str | None = None
    party_name: str | None = None
    party_type: Literal["business", "individual"] | None = None
    party_address: AddressConfig | None = None
    account_details: list[AccountDetailConfig] = []
    routing_details: list[RoutingDetailConfig] = []

    @model_validator(mode="after")
    def _apply_sandbox_behavior(self) -> CounterpartyAccountConfig:
        if self.wallet_account_number_type is not None and self.sandbox_behavior is not None:
            raise ValueError(
                "Counterparty inline account: choose sandbox_behavior (ACH/bank test "
                "accounts) or wallet_account_number_type (stablecoin wallet) — not both."
            )
        if self.wallet_account_number_type is not None:
            demo = SANDBOX_WALLET_DEMO_ADDRESSES[self.wallet_account_number_type]
            self.account_details = [
                AccountDetailConfig(
                    account_number=demo,
                    account_number_type=self.wallet_account_number_type,
                )
            ]
            self.routing_details = []
            return self

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
    currency_exponent: int | None = None
    description: str | None = None
    ledgerable_type: Literal["external_account", "internal_account", "virtual_account"] | None = (
        None
    )
    ledgerable_id: RefStr | None = None

    @model_validator(mode="before")
    @classmethod
    def _inject_currency_exponent(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("currency_exponent") is not None:
            return data
        currency = data.get("currency")
        if isinstance(currency, str):
            exp = implied_ledger_currency_exponent(currency)
            if exp is not None:
                return {**data, "currency_exponent": exp}
        return data


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
    status: (
        Literal[
            "active",
            "closed",
            "pending_closure",
            "pending_activation",
            "suspended",
        ]
        | None
    ) = None
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
    currency_exponent: int | None = None
    description: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _inject_currency_exponent(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if data.get("currency_exponent") is not None:
            return data
        currency = data.get("currency")
        if isinstance(currency, str):
            exp = implied_ledger_currency_exponent(currency)
            if exp is not None:
                return {**data, "currency_exponent": exp}
        return data


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
    subtype: (
        Literal[
            "CCD",
            "PPD",
            "IAT",
            "CTX",
            "WEB",
            "CIE",
            "TEL",
        ]
        | None
    ) = None
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


