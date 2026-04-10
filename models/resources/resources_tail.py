"""MT resource models — layers 6–7 (lifecycle, settlements)."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from models.shared import (
    DisplayPhase,
    InlineLedgerTransactionConfig,
    MetadataMixin,
    RefStr,
    ReversalReason,
    _BaseResourceConfig,
)

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


class VerifyExternalAccountConfig(MetadataMixin, _BaseResourceConfig):
    """Micro-deposit verification initiation — emitted from ``funds_flows`` steps."""

    display_phase: ClassVar[int] = DisplayPhase.LIFECYCLE
    resource_type: ClassVar[str] = "verify_external_account"

    external_account_ref: RefStr
    originating_account_id: RefStr
    type: str = "rtp"
    currency: str | None = None
    priority: Literal["high", "normal"] | None = None


class CompleteVerificationConfig(MetadataMixin, _BaseResourceConfig):
    """Complete micro-deposit verification — emitted from ``funds_flows`` steps."""

    display_phase: ClassVar[int] = DisplayPhase.LIFECYCLE
    resource_type: ClassVar[str] = "complete_verification"

    external_account_ref: RefStr
    staged: bool = False


class ArchiveResourceConfig(MetadataMixin, _BaseResourceConfig):
    """Archive or delete another resource — emitted from ``funds_flows`` steps."""

    display_phase: ClassVar[int] = DisplayPhase.MUTATIONS
    resource_type: ClassVar[str] = "archive_resource"

    object_resource_type: str = Field(
        ...,
        validation_alias="resource_type",
        serialization_alias="resource_type",
        description="Resource type of the row being archived (e.g. incoming_payment_detail).",
    )
    resource_ref: RefStr
    archive_method: Literal["delete", "archive", "request_closure"] = "delete"


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
    relationship_types: list[Literal["beneficial_owner", "control_person"]] | None = None
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
