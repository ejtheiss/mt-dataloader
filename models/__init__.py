"""Pydantic schema layer for the Modern Treasury Dataloader.

Defines every config type, shared type, internal return type, and app setting.
All other modules import from here — no domain types are defined elsewhere.

This package re-exports the full public API so that ``from models import X``
continues to work unchanged after the monolith → package decomposition.
"""

# --- shared types & constants ---
# --- top-level config ---
from models.config import DataLoaderConfig
from models.current_user import AppUserRole, CurrentAppUser, coerce_app_user_role

# --- flow DSL + generation ---
from models.flow_dsl import (
    ActorDatasetOverride,
    ActorFrame,
    ActorSlot,
    ApplicabilityRule,
    EdgeCaseOverride,
    FundFlowViewConfig,
    FundsFlowConfig,
    FundsFlowScaleConfig,
    GenerationRecipeV1,
    LedgerViewConfig,
    OptionalGroupConfig,
    PaymentMixConfig,
    PaymentsViewConfig,
    StepMatch,
)
from models.loader_draft import LoaderDraft
from models.loader_setup_json import (
    LOADER_SETUP_SCHEMA_VERSION,
    ApplyConfigPatchJsonRequestV1,
    LoaderSetupEnvelopeV1,
    LoaderSetupErrorItem,
    LoaderSetupFlowDiagnosticItem,
    LoaderSetupWarningItem,
    RevalidateJsonRequestV1,
)

# --- manifest (run JSON on disk) ---
from models.manifest import FailedEntry, ManifestEntry, RunManifest, StagedEntry

# --- resource configs (Layers 0–6) ---
from models.resources import (
    ArchiveResourceConfig,
    BalanceMonitorAlertCondition,
    CategoryMembershipConfig,
    CompleteVerificationConfig,
    ConnectionConfig,
    CounterpartyAccountConfig,
    CounterpartyConfig,
    ExpectedPaymentConfig,
    ExternalAccountConfig,
    IncomingPaymentDetailConfig,
    InternalAccountConfig,
    LedgerAccountBalanceMonitorConfig,
    LedgerAccountCategoryConfig,
    LedgerAccountConfig,
    LedgerAccountSettlementConfig,
    LedgerAccountStatementConfig,
    LedgerConfig,
    LedgerTransactionConfig,
    LegalEntityAssociationConfig,
    LegalEntityConfig,
    LineItemConfig,
    NestedCategoryConfig,
    PaymentOrderConfig,
    ReconciliationRuleVariable,
    ReturnConfig,
    ReversalConfig,
    TransactionConfig,
    TransitionLedgerTransactionConfig,
    VerifyExternalAccountConfig,
    VirtualAccountConfig,
)
from models.run_list import RunListRow

# --- runtime types ---
from models.runtime import HandlerResult

# --- app settings ---
from models.settings import AppSettings
from models.shared import (
    REF_PATTERN,
    RESOURCE_TYPES,
    SOURCE_BADGE,
    AccountDetailConfig,
    AddressConfig,
    AmountCents,
    CurrencyCode,
    DisplayPhase,
    InlineLedgerAccountConfig,
    InlineLedgerEntryConfig,
    InlineLedgerTransactionConfig,
    LedgerStatus,
    MetadataMixin,
    PaymentDirection,
    RefStr,
    RoutingDetailConfig,
    WalletAccountNumberType,
    _BaseResourceConfig,
)

# --- step models + derived constants ---
from models.steps import (
    ARROW_BY_TYPE,
    INLINE_LT_TYPES,
    NEEDS_PAYMENT_TYPE,
    PAYMENT_MIX_TYPE_MAP,
    RESOURCE_TYPE_TO_SECTION,
    REVERSES_DIRECTION,
    VALID_STEP_TYPES,
    ArchiveResourceStep,
    CompleteVerificationStep,
    ExpectedPaymentStep,
    FundsFlowStep,
    FundsFlowStepConfig,
    IncomingPaymentDetailStep,
    LedgerTransactionStep,
    PaymentOrderStep,
    ReturnStep,
    ReversalStep,
    TransitionLedgerTransactionStep,
    VerifyExternalAccountStep,
    _extract_step_ref,
    _LedgerableMixin,
    _LifecycleLedgerMixin,
    _StepBase,
)

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
    "_BaseResourceConfig",
    "AddressConfig",
    "AccountDetailConfig",
    "RoutingDetailConfig",
    "WalletAccountNumberType",
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
    "VerifyExternalAccountConfig",
    "CompleteVerificationConfig",
    "ArchiveResourceConfig",
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
    "RunListRow",
    # App user stub (Plan 0 roles)
    "AppUserRole",
    "CurrentAppUser",
    "coerce_app_user_role",
    "LoaderDraft",
    # Loader setup JSON API v1 (validate-json / config/save)
    "LOADER_SETUP_SCHEMA_VERSION",
    "LoaderSetupEnvelopeV1",
    "LoaderSetupErrorItem",
    "LoaderSetupWarningItem",
    "LoaderSetupFlowDiagnosticItem",
    "RevalidateJsonRequestV1",
    "ApplyConfigPatchJsonRequestV1",
    # App settings
    "AppSettings",
]
