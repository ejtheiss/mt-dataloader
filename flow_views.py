"""Frozen view data types and compute_view_data pass for fund flow views.

Produces per-view row/column data from FlowIR + actors, consumed by
templates (Plan 4 Phases 2-3) and stored on CompilationContext.view_data.

Types are all frozen dataclasses. compute_view_data is a pure function
that can be called standalone or slotted as a pipeline pass.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import TYPE_CHECKING

from flow_compiler import (
    FlowIR,
    FlowIRStep,
    _build_ref_display_map,
    _ref_account_type,
    _resolve_actor_display,
    flatten_actor_refs,
)

if TYPE_CHECKING:
    from models import FundsFlowConfig

__all__ = [
    "LedgerColumnDef",
    "PaymentColumnDef",
    "LedgerEntryPlacement",
    "AccountImpact",
    "LedgerViewRow",
    "PaymentViewRow",
    "FlowViewData",
    "compute_view_data",
]

# ---------------------------------------------------------------------------
# Frozen data types — all immutable, safe to cache/serialize
# ---------------------------------------------------------------------------

_PAYMENT_RESOURCE_TYPES = frozenset({
    "payment_order", "incoming_payment_detail",
    "expected_payment", "return", "reversal",
})

_SOURCE_BADGE: dict[str, str] = {
    "payment_order": "PO",
    "incoming_payment_detail": "IPD",
    "expected_payment": "EP",
    "return": "Ret",
    "reversal": "Rev",
}


@dataclass(frozen=True)
class LedgerColumnDef:
    """One ledger account column in the Ledger View."""

    account_ref: str
    display_name: str
    normal_balance: str | None = None


@dataclass(frozen=True)
class PaymentColumnDef:
    """One account column in the Payments View (IA, EA, or VA)."""

    account_ref: str
    display_name: str
    account_type: str
    parent_ref: str | None = None
    connection: str | None = None
    currency: str | None = None


@dataclass(frozen=True)
class LedgerEntryPlacement:
    """One ledger entry cell (column + direction + amount)."""

    column_ref: str
    direction: str
    amount: int


@dataclass(frozen=True)
class AccountImpact:
    """One payment account cell (column + in/out + amount)."""

    column_ref: str
    direction: str
    amount: int


@dataclass(frozen=True)
class LedgerViewRow:
    """One row in the Ledger View — maps to one LedgerTransaction."""

    step_ref: str
    description: str
    status: str
    effective_at: str | None = None
    ledgerable_type: str | None = None
    ledgerable_ref: str | None = None
    entries: tuple[LedgerEntryPlacement, ...] = ()
    payment_account_impacts: tuple[AccountImpact, ...] = ()
    is_standalone: bool = False
    optional_group: str | None = None


@dataclass(frozen=True)
class PaymentViewRow:
    """One row in the Payments View — maps to one payment resource."""

    step_ref: str
    description: str
    resource_type: str
    status: str
    amount: int
    direction: str | None = None
    payment_type: str | None = None
    has_auto_lt: bool = False
    account_impacts: tuple[AccountImpact, ...] = ()
    child_lt_rows: tuple[LedgerViewRow, ...] = ()
    optional_group: str | None = None


@dataclass(frozen=True)
class FlowViewData:
    """Pre-computed data for rendering fund flow views."""

    ledger_rows: tuple[LedgerViewRow, ...] = ()
    ledger_columns: tuple[LedgerColumnDef, ...] = ()
    payment_columns: tuple[PaymentColumnDef, ...] = ()
    payment_rows: tuple[PaymentViewRow, ...] = ()
    payment_view_columns: tuple[PaymentColumnDef, ...] = ()
    available_views: tuple[str, ...] = ()
    account_actor_map: dict[str, str] = dataclasses.field(default_factory=dict)


# ---------------------------------------------------------------------------
# Column builders — derive from flow actors
# ---------------------------------------------------------------------------


def _build_ledger_columns(
    flow_config: FundsFlowConfig,
    explicit_refs: list[str] | None = None,
) -> tuple[LedgerColumnDef, ...]:
    """Build ledger account columns from the flow's actors.

    If *explicit_refs* is provided (from view_config), only those accounts
    are included in that order. Otherwise, all ledger_account actors are used.
    """
    ref_display_map = _build_ref_display_map(flow_config.actors)
    if explicit_refs:
        cols = []
        for ref in explicit_refs:
            display = _resolve_actor_display(ref, ref_display_map)
            cols.append(LedgerColumnDef(account_ref=ref, display_name=display))
        return tuple(cols)

    cols = []
    for ref, display in ref_display_map.items():
        if _ref_account_type(ref) == "ledger_account":
            cols.append(LedgerColumnDef(
                account_ref=ref,
                display_name=display,
            ))
    return tuple(cols)


def _build_payment_columns(
    flow_config: FundsFlowConfig,
    explicit_refs: list[str] | None = None,
) -> tuple[PaymentColumnDef, ...]:
    """Build payment account columns (IA, EA, VA) from the flow's actors."""
    ref_display_map = _build_ref_display_map(flow_config.actors)
    if explicit_refs:
        cols = []
        for ref in explicit_refs:
            acct_type = _ref_account_type(ref)
            display = _resolve_actor_display(ref, ref_display_map)
            cols.append(PaymentColumnDef(
                account_ref=ref,
                display_name=display,
                account_type=acct_type,
            ))
        return tuple(cols)

    cols = []
    for ref, display in ref_display_map.items():
        acct_type = _ref_account_type(ref)
        if acct_type in ("internal_account", "external_account", "virtual_account"):
            cols.append(PaymentColumnDef(
                account_ref=ref,
                display_name=display,
                account_type=acct_type,
            ))
    return tuple(cols)


# ---------------------------------------------------------------------------
# Row builders — derive from FlowIR steps
# ---------------------------------------------------------------------------


def _build_ledger_rows(
    flow_ir: FlowIR,
    actors: dict[str, str],
    ledger_columns: tuple[LedgerColumnDef, ...],
    og_step_ids: dict[str, str] | None = None,
) -> tuple[LedgerViewRow, ...]:
    """Build Ledger View rows from FlowIR steps."""
    col_refs = {c.account_ref for c in ledger_columns}
    _og = og_step_ids or {}
    rows: list[LedgerViewRow] = []

    for step in flow_ir.steps:
        if step.resource_type == "transition_ledger_transaction":
            continue

        for lg in step.ledger_groups:
            placements: list[LedgerEntryPlacement] = []
            for entry in lg.entries:
                acct_ref = entry.get("ledger_account_id", "")
                if acct_ref in col_refs:
                    placements.append(LedgerEntryPlacement(
                        column_ref=acct_ref,
                        direction=entry.get("direction", "debit"),
                        amount=entry.get("amount", 0),
                    ))

            is_standalone = step.resource_type == "ledger_transaction"
            ledgerable_type = None if is_standalone else step.resource_type
            ledgerable_ref = None if is_standalone else step.emitted_ref

            rows.append(LedgerViewRow(
                step_ref=step.emitted_ref,
                description=step.payload.get("description", step.step_id),
                status=lg.status or step.payload.get("ledger_status", "pending"),
                effective_at=step.payload.get("effective_at"),
                ledgerable_type=ledgerable_type,
                ledgerable_ref=ledgerable_ref,
                entries=tuple(placements),
                is_standalone=is_standalone,
                optional_group=_og.get(step.step_id),
            ))

    return tuple(rows)


def _resolve_payment_impacts(
    step: FlowIRStep,
    actors: dict[str, str],
    payment_columns: tuple[PaymentColumnDef, ...],
) -> tuple[AccountImpact, ...]:
    """Resolve which payment account columns a step impacts and in which direction."""
    col_refs = {c.account_ref for c in payment_columns}
    impacts: list[AccountImpact] = []
    payload = step.payload
    rtype = step.resource_type
    amount = payload.get("amount", 0)

    if rtype == "payment_order":
        direction = payload.get("direction", "credit")
        orig_ref = payload.get("originating_account_id", "")
        recv_ref = payload.get("receiving_account_id", "")

        if direction == "credit":
            if orig_ref in col_refs:
                impacts.append(AccountImpact(column_ref=orig_ref, direction="out", amount=amount))
            if recv_ref and recv_ref in col_refs:
                impacts.append(AccountImpact(column_ref=recv_ref, direction="in", amount=amount))
        else:
            if recv_ref and recv_ref in col_refs:
                impacts.append(AccountImpact(column_ref=recv_ref, direction="out", amount=amount))
            if orig_ref in col_refs:
                impacts.append(AccountImpact(column_ref=orig_ref, direction="in", amount=amount))

    elif rtype == "incoming_payment_detail":
        ia_ref = payload.get("internal_account_id", "")
        if ia_ref in col_refs:
            impacts.append(AccountImpact(column_ref=ia_ref, direction="in", amount=amount))

    elif rtype == "expected_payment":
        ia_ref = payload.get("internal_account_id", "")
        if ia_ref in col_refs:
            impacts.append(AccountImpact(column_ref=ia_ref, direction="in", amount=amount))

    elif rtype in ("return", "reversal"):
        pass

    return tuple(impacts)


def _build_child_lt_rows(
    step: FlowIRStep,
    ledger_columns: tuple[LedgerColumnDef, ...],
) -> tuple[LedgerViewRow, ...]:
    """Build child LT rows from a payment step's ledger groups."""
    col_refs = {c.account_ref for c in ledger_columns}
    child_rows: list[LedgerViewRow] = []

    for lg in step.ledger_groups:
        placements: list[LedgerEntryPlacement] = []
        for entry in lg.entries:
            acct_ref = entry.get("ledger_account_id", "")
            if acct_ref in col_refs:
                placements.append(LedgerEntryPlacement(
                    column_ref=acct_ref,
                    direction=entry.get("direction", "debit"),
                    amount=entry.get("amount", 0),
                ))

        child_rows.append(LedgerViewRow(
            step_ref=f"{step.emitted_ref}__{lg.group_id}",
            description=step.payload.get("description", step.step_id),
            status=lg.status or step.payload.get("ledger_status", "pending"),
            ledgerable_type=step.resource_type,
            ledgerable_ref=step.emitted_ref,
            entries=tuple(placements),
            is_standalone=False,
        ))

    return tuple(child_rows)


def _build_payment_rows(
    flow_ir: FlowIR,
    actors: dict[str, str],
    payment_columns: tuple[PaymentColumnDef, ...],
    ledger_columns: tuple[LedgerColumnDef, ...] = (),
    og_step_ids: dict[str, str] | None = None,
) -> tuple[PaymentViewRow, ...]:
    """Build Payments View rows from FlowIR steps."""
    _og = og_step_ids or {}
    rows: list[PaymentViewRow] = []

    for step in flow_ir.steps:
        if step.resource_type not in _PAYMENT_RESOURCE_TYPES:
            if step.resource_type == "transition_ledger_transaction":
                continue
            if step.resource_type == "ledger_transaction":
                continue

        impacts = _resolve_payment_impacts(step, actors, payment_columns)
        has_lt = len(step.ledger_groups) > 0
        child_lts = _build_child_lt_rows(step, ledger_columns) if has_lt else ()

        rows.append(PaymentViewRow(
            step_ref=step.emitted_ref,
            description=step.payload.get("description", step.step_id),
            resource_type=step.resource_type,
            status=step.payload.get("status", "pending"),
            amount=step.payload.get("amount", 0),
            direction=step.payload.get("direction"),
            payment_type=step.payload.get("type") or step.payload.get("payment_type"),
            has_auto_lt=has_lt,
            account_impacts=impacts,
            child_lt_rows=child_lts,
            optional_group=_og.get(step.step_id),
        ))

    return tuple(rows)


def _build_account_actor_map(
    flow_config: FundsFlowConfig,
) -> dict[str, str]:
    """Build account_ref → frame.slot key mapping from the flow's actors."""
    return {ref: key for key, ref in flatten_actor_refs(flow_config.actors).items()}


# ---------------------------------------------------------------------------
# Main compute function
# ---------------------------------------------------------------------------


def compute_view_data(
    flow_ir: FlowIR,
    flow_config: FundsFlowConfig,
) -> FlowViewData:
    """Compute per-view row/column data from FlowIR + flow config.

    Auto-derives columns from actors when view_config doesn't specify
    explicit account lists. Returns an empty FlowViewData when no actors
    are declared.
    """
    actors = flow_config.actors or {}
    if not actors:
        return FlowViewData()

    ref_map = flatten_actor_refs(actors)

    og_step_ids: dict[str, str] = {}
    for og in flow_config.optional_groups:
        for s in og.steps:
            og_step_ids[s.step_id] = og.label

    vc = flow_config.view_config
    available: list[str] = []

    has_ledger_actors = any(
        _ref_account_type(ref) == "ledger_account"
        for ref in ref_map.values()
    )
    has_payment_actors = any(
        _ref_account_type(ref) in ("internal_account", "external_account", "virtual_account")
        for ref in ref_map.values()
    )

    explicit_ledger_refs = None
    explicit_payment_refs = None
    if vc:
        if vc.ledger_view:
            explicit_ledger_refs = vc.ledger_view.account_columns or None
        if vc.payments_view:
            explicit_payment_refs = vc.payments_view.account_columns or None

    ledger_cols: tuple[LedgerColumnDef, ...] = ()
    ledger_rows: tuple[LedgerViewRow, ...] = ()
    if has_ledger_actors or explicit_ledger_refs:
        available.append("ledger")
        ledger_cols = _build_ledger_columns(flow_config, explicit_ledger_refs)
        ledger_rows = _build_ledger_rows(flow_ir, ref_map, ledger_cols, og_step_ids)

    payment_cols: tuple[PaymentColumnDef, ...] = ()
    payment_rows: tuple[PaymentViewRow, ...] = ()
    if has_payment_actors or explicit_payment_refs:
        available.append("payments")
        payment_cols = _build_payment_columns(flow_config, explicit_payment_refs)
        payment_rows = _build_payment_rows(flow_ir, ref_map, payment_cols, ledger_cols, og_step_ids)

    return FlowViewData(
        ledger_rows=ledger_rows,
        ledger_columns=ledger_cols,
        payment_columns=payment_cols if has_ledger_actors else (),
        payment_rows=payment_rows,
        payment_view_columns=payment_cols if has_payment_actors else (),
        available_views=tuple(available),
        account_actor_map=_build_account_actor_map(flow_config),
    )
