"""Top-level DataLoaderConfig — the root schema parsed from the user's JSON."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from models.flow_dsl import FundsFlowConfig
from models.resources import (
    ArchiveResourceConfig,
    CategoryMembershipConfig,
    CompleteVerificationConfig,
    ConnectionConfig,
    CounterpartyConfig,
    ExpectedPaymentConfig,
    ExternalAccountConfig,
    IncomingPaymentDetailConfig,
    InternalAccountConfig,
    LedgerAccountCategoryConfig,
    LedgerAccountConfig,
    LedgerConfig,
    LedgerTransactionConfig,
    LegalEntityConfig,
    NestedCategoryConfig,
    PaymentOrderConfig,
    ReturnConfig,
    ReversalConfig,
    TransitionLedgerTransactionConfig,
    VerifyExternalAccountConfig,
    VirtualAccountConfig,
)


def _connection_key_from_ref(connection_id: str) -> str | None:
    prefix = "$ref:connection."
    if not connection_id.startswith(prefix):
        return None
    return connection_id.removeprefix(prefix)


def _expected_ia_currency_for_connection_ref(conn_ref: str) -> str | None:
    """Infer the internal-account currency MT expects for named sandbox rails.

    USDG PSP connections must back USDG internal accounts; USD PSP / ``*_usd``
    rails expect USD. Mismatches commonly surface as 422
    ``Payment type, direction, and currency combination is not supported``.
    """
    low = conn_ref.lower()
    if "usdg" in low:
        return "USDG"
    if low.endswith("_usd") or "psp_usd" in low:
        return "USD"
    return None


def _check_ia_currency_vs_connection(
    *,
    label: str,
    connection_id: str,
    currency: str,
    conn_by_ref: dict[str, ConnectionConfig],
) -> None:
    key = _connection_key_from_ref(connection_id)
    if key is None:
        return
    conn = conn_by_ref.get(key)
    if conn is None:
        return
    expected = _expected_ia_currency_for_connection_ref(conn.ref)
    if expected is None or currency == expected:
        return
    raise ValueError(
        f"{label}: currency {currency!r} is incompatible with connection "
        f"{conn.ref!r} (expected {expected} for this rail). "
        f"Modern Treasury rejects mismatched internal-account currency vs "
        f"connection with errors like 'Payment type, direction, and currency "
        f"combination is not supported for this account'."
    )


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
    transition_ledger_transactions: list[TransitionLedgerTransactionConfig] = []

    # Lifecycle rows emitted by the compiler from funds_flows steps (may also
    # appear in hand-merged / pre-compiled JSON).
    verify_external_accounts: list[VerifyExternalAccountConfig] = []
    complete_verifications: list[CompleteVerificationConfig] = []
    archive_resources: list[ArchiveResourceConfig] = []

    # Display / branding
    customer_name: str = Field(
        default="direct",
        description=(
            "Label used for customer-facing account participants in "
            "Mermaid diagrams, e.g. '{{ customer_name }} Account'. "
            "Defaults to 'direct'."
        ),
    )

    # Funds Flow DSL (compiler input, not an MT resource).
    # Intentionally skipped by _refs_are_unique_within_type — these items
    # have no resource_type ClassVar.  The compiler validates flow refs.
    funds_flows: list[FundsFlowConfig] = Field(
        default_factory=list,
        description=(
            "High-level funds flow definitions. Compiled to FlowIR and "
            "emitted into the resource sections above. If empty, the "
            "config is treated as a raw resource config (passthrough)."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _drop_ipd_ep_originating_account_on_raw_resources(cls, data: Any) -> Any:
        """LLMs often copy PO-style `originating_account_id` onto raw IPD/EP rows.

        The Funds Flow DSL allows `originating_account_id` on IPD/EP *steps*; the
        compiler strips it when emitting. Raw ``incoming_payment_details`` and
        ``expected_payments`` entries use the narrower resource schema (no
        ``originating_account_id``), so we discard the key here to match emit
        behavior and avoid spurious validation failures.
        """
        if not isinstance(data, dict):
            return data
        out = dict(data)
        for key in ("incoming_payment_details", "expected_payments"):
            items = out.get(key)
            if not isinstance(items, list):
                continue
            coerced: list[Any] = []
            changed = False
            for item in items:
                if isinstance(item, dict) and "originating_account_id" in item:
                    coerced.append({k: v for k, v in item.items() if k != "originating_account_id"})
                    changed = True
                else:
                    coerced.append(item)
            if changed:
                out[key] = coerced
        return out

    @model_validator(mode="after")
    def _refs_are_unique_within_type(self) -> DataLoaderConfig:
        """Catch duplicate refs before the engine even sees them."""
        seen: dict[str, str] = {}
        for section_name in type(self).model_fields:
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

    @model_validator(mode="after")
    def _omit_legal_entity_connection_id_when_sole_modern_treasury(self) -> DataLoaderConfig:
        """Drop authored LE ``connection_id`` when there is only one MT connection row.

        Modern Treasury infers the connection for that case; we omit the field on
        create. Multiple connections still require an explicit ``connection_id``
        (filled by the executor when absent — see ``inject_legal_entity_psp_connection_id``).
        """
        if not legal_entity_omit_connection_id_on_create(self):
            return self
        if not any(le.connection_id is not None for le in self.legal_entities):
            return self
        cleared = [le.model_copy(update={"connection_id": None}) for le in self.legal_entities]
        return self.model_copy(update={"legal_entities": cleared})

    @model_validator(mode="after")
    def _internal_account_currency_matches_connection_rail(self) -> DataLoaderConfig:
        """Align IA currency with PSP connection naming (USD vs USDG rails)."""
        conn_by_ref = {c.ref: c for c in self.connections}
        for ia in self.internal_accounts:
            _check_ia_currency_vs_connection(
                label=f"internal_account '{ia.ref}'",
                connection_id=ia.connection_id,
                currency=ia.currency,
                conn_by_ref=conn_by_ref,
            )
        for flow in self.funds_flows:
            ir = flow.instance_resources
            if not ir:
                continue
            for raw_ia in ir.get("internal_accounts") or []:
                if not isinstance(raw_ia, dict):
                    continue
                ref = raw_ia.get("ref")
                cid = raw_ia.get("connection_id")
                cur = raw_ia.get("currency")
                if ref is None or not cid or not cur:
                    continue
                _check_ia_currency_vs_connection(
                    label=(
                        f"funds_flow '{flow.ref}' instance_resources internal_accounts ref {ref!r}"
                    ),
                    connection_id=cid,
                    currency=cur,
                    conn_by_ref=conn_by_ref,
                )
        return self


def legal_entity_omit_connection_id_on_create(config: DataLoaderConfig) -> bool:
    """True when legal-entity create should omit ``connection_id`` (sole PSP row)."""
    conns = config.connections
    return len(conns) == 1 and conns[0].entity_id == "modern_treasury"
