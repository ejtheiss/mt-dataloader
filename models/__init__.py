"""Pydantic schema layer for the Modern Treasury Dataloader.

Defines every config type, shared type, internal return type, and app setting.
All other modules import from here — no domain types are defined elsewhere.

This package re-exports the full public API so that ``from models import X``
continues to work unchanged after the monolith → package decomposition.
"""

# --- shared types & constants ---
from models.shared import (
    REF_PATTERN,
    RESOURCE_TYPES,
    SOURCE_BADGE,
    RefStr,
    DisplayPhase,
    MetadataMixin,
    AddressConfig,
    AccountDetailConfig,
    RoutingDetailConfig,
    InlineLedgerAccountConfig,
    InlineLedgerEntryConfig,
    InlineLedgerTransactionConfig,
    AmountCents,
    CurrencyCode,
    PaymentDirection,
    LedgerStatus,
    _BaseResourceConfig,
)

# --- resource configs (Layers 0–6) ---
from models.resources import (
    ConnectionConfig,
    LegalEntityConfig,
    LedgerConfig,
    CounterpartyAccountConfig,
    CounterpartyConfig,
    LedgerAccountConfig,
    InternalAccountConfig,
    ExternalAccountConfig,
    LedgerAccountCategoryConfig,
    VirtualAccountConfig,
    ReconciliationRuleVariable,
    ExpectedPaymentConfig,
    LineItemConfig,
    PaymentOrderConfig,
    IncomingPaymentDetailConfig,
    LedgerTransactionConfig,
    ReturnConfig,
    ReversalConfig,
    CategoryMembershipConfig,
    NestedCategoryConfig,
    TransitionLedgerTransactionConfig,
    LedgerAccountSettlementConfig,
    BalanceMonitorAlertCondition,
    LedgerAccountBalanceMonitorConfig,
    LedgerAccountStatementConfig,
    LegalEntityAssociationConfig,
    TransactionConfig,
)

# --- step models + derived constants ---
from models.steps import (
    _StepBase,
    _LedgerableMixin,
    _LifecycleLedgerMixin,
    PaymentOrderStep,
    IncomingPaymentDetailStep,
    ExpectedPaymentStep,
    LedgerTransactionStep,
    ReturnStep,
    ReversalStep,
    TransitionLedgerTransactionStep,
    VerifyExternalAccountStep,
    CompleteVerificationStep,
    ArchiveResourceStep,
    FundsFlowStep,
    FundsFlowStepConfig,
    VALID_STEP_TYPES,
    RESOURCE_TYPE_TO_SECTION,
    ARROW_BY_TYPE,
    NEEDS_PAYMENT_TYPE,
    INLINE_LT_TYPES,
    PAYMENT_MIX_TYPE_MAP,
    REVERSES_DIRECTION,
    _extract_step_ref,
)

# --- flow DSL + generation ---
from models.flow_dsl import (
    ActorSlot,
    ActorFrame,
    ActorDatasetOverride,
    ApplicabilityRule,
    EdgeCaseOverride,
    OptionalGroupConfig,
    StepMatch,
    FundsFlowScaleConfig,
    LedgerViewConfig,
    PaymentsViewConfig,
    FundFlowViewConfig,
    FundsFlowConfig,
    PaymentMixConfig,
    GenerationRecipeV1,
)

# --- top-level config ---
from models.config import DataLoaderConfig

# --- runtime types ---
from models.runtime import HandlerResult

# --- manifest (run JSON on disk) ---
from models.manifest import FailedEntry, ManifestEntry, RunManifest, StagedEntry

# --- app settings ---
from models.settings import AppSettings

__all__ = [
    # Constants & custom types
    "REF_PATTERN",
    "RESOURCE_TYPES",
    "SOURCE_BADGE",
    "RefStr",
    "DisplayPhase",
    # Reusable Annotated types
    "AmountCents",
    "CurrencyCode",
    "PaymentDirection",
    "LedgerStatus",
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
    "TransitionLedgerTransactionConfig",
    # New resource types (feature audit)
    "LedgerAccountSettlementConfig",
    "BalanceMonitorAlertCondition",
    "LedgerAccountBalanceMonitorConfig",
    "LedgerAccountStatementConfig",
    "LegalEntityAssociationConfig",
    "TransactionConfig",
    # Funds Flow DSL — Typed Step Models
    "_StepBase",
    "_LedgerableMixin",
    "_LifecycleLedgerMixin",
    "PaymentOrderStep",
    "IncomingPaymentDetailStep",
    "ExpectedPaymentStep",
    "LedgerTransactionStep",
    "ReturnStep",
    "ReversalStep",
    "TransitionLedgerTransactionStep",
    "VerifyExternalAccountStep",
    "CompleteVerificationStep",
    "ArchiveResourceStep",
    "FundsFlowStep",
    "FundsFlowStepConfig",
    # Derived constants
    "VALID_STEP_TYPES",
    "RESOURCE_TYPE_TO_SECTION",
    "ARROW_BY_TYPE",
    "NEEDS_PAYMENT_TYPE",
    "INLINE_LT_TYPES",
    "PAYMENT_MIX_TYPE_MAP",
    "REVERSES_DIRECTION",
    "_extract_step_ref",
    # Actor Frames & Slots
    "ActorSlot",
    "ActorFrame",
    "ActorDatasetOverride",
    # Flow-level DSL
    "ApplicabilityRule",
    "EdgeCaseOverride",
    "OptionalGroupConfig",
    "StepMatch",
    "FundsFlowScaleConfig",
    "LedgerViewConfig",
    "PaymentsViewConfig",
    "FundFlowViewConfig",
    "FundsFlowConfig",
    # Generation
    "PaymentMixConfig",
    "GenerationRecipeV1",
    # Top-level config
    "DataLoaderConfig",
    # Internal types
    "HandlerResult",
    "ManifestEntry",
    "FailedEntry",
    "StagedEntry",
    "RunManifest",
    # App settings
    "AppSettings",
]
