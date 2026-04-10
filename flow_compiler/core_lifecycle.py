"""Lifecycle graph helpers: return / reversal / transition ledger links."""

from __future__ import annotations

import dataclasses

from models import (
    ExpectedPaymentStep,
    IncomingPaymentDetailStep,
    LedgerTransactionStep,
    PaymentOrderStep,
    ReturnStep,
    ReversalStep,
    TransitionLedgerTransactionStep,
    _StepBase,
)

from .ir import FlowIRStep


def _auto_derive_lifecycle_refs(
    step: _StepBase,
    step_dict: dict,
    step_ref_map: dict[str, str],
    all_steps: list[_StepBase],
) -> None:
    """Auto-set returnable_id / payment_order_id / ledger_transaction_id from depends_on targets."""
    if isinstance(step, ReturnStep) and "returnable_id" not in step_dict:
        for dep_id in step.depends_on:
            dep_step = next((s for s in all_steps if s.step_id == dep_id), None)
            if dep_step is None:
                continue
            if isinstance(dep_step, IncomingPaymentDetailStep):
                step_dict["returnable_id"] = step_ref_map[dep_id]
                step_dict.setdefault("returnable_type", "incoming_payment_detail")
                break
            if isinstance(dep_step, PaymentOrderStep):
                step_dict["returnable_id"] = step_ref_map[dep_id]
                step_dict.setdefault("returnable_type", "payment_order")
                break

    if isinstance(step, ReversalStep) and "payment_order_id" not in step_dict:
        for dep_id in step.depends_on:
            dep_step = next((s for s in all_steps if s.step_id == dep_id), None)
            if dep_step is None:
                continue
            if isinstance(dep_step, PaymentOrderStep):
                step_dict["payment_order_id"] = step_ref_map[dep_id]
                break

    if (
        isinstance(step, TransitionLedgerTransactionStep)
        and "ledger_transaction_id" not in step_dict
    ):
        for dep_id in step.depends_on:
            dep_step = next((s for s in all_steps if s.step_id == dep_id), None)
            if dep_step is None:
                continue
            if isinstance(dep_step, LedgerTransactionStep):
                step_dict["ledger_transaction_id"] = step_ref_map[dep_id]
                break
            if isinstance(
                dep_step, (PaymentOrderStep, ExpectedPaymentStep, ReturnStep, ReversalStep)
            ):
                entries = getattr(dep_step, "ledger_entries", None)
                inline = getattr(dep_step, "ledger_inline", False)
                if isinstance(entries, list) and entries and inline:
                    parent_ref = step_ref_map[dep_id]
                    step_dict["ledger_transaction_id"] = f"{parent_ref}.ledger_transaction"
                    break


def _find_reverse_target(
    step: _StepBase,
    all_steps: list[_StepBase],
) -> _StepBase | None:
    """Find the parent step whose ledger entries should be reversed."""
    target_id: str | None = None
    if isinstance(step, ReturnStep) and step.returnable_id:
        target_id = step.returnable_id
    elif isinstance(step, ReversalStep) and step.payment_order_id:
        target_id = step.payment_order_id

    if target_id:
        match = next((s for s in all_steps if s.step_id == target_id), None)
        if match:
            return match

    for dep_id in step.depends_on:
        dep = next((s for s in all_steps if s.step_id == dep_id), None)
        if dep:
            dep_entries = getattr(dep, "ledger_entries", None)
            if isinstance(dep_entries, list) and dep_entries:
                return dep
    return None


def _flip_entry(entry_dict: dict) -> dict:
    """Return a copy of a ledger entry dict with direction flipped."""
    flipped = dict(entry_dict)
    if flipped.get("direction") == "debit":
        flipped["direction"] = "credit"
    elif flipped.get("direction") == "credit":
        flipped["direction"] = "debit"
    return flipped


def _with_lifecycle_depends_on(step: FlowIRStep) -> FlowIRStep:
    """Return step with lifecycle depends_on edges added.

    Pure function — returns a new FlowIRStep via dataclasses.replace()
    if edges need adding, otherwise returns the original object.
    """
    extra: list[str] = []

    if step.resource_type == "return":
        returnable_ref = step.payload.get("returnable_id", "")
        if returnable_ref.startswith("$ref:") and returnable_ref not in step.depends_on:
            extra.append(returnable_ref)
    elif step.resource_type == "reversal":
        po_ref = step.payload.get("payment_order_id", "")
        if po_ref.startswith("$ref:") and po_ref not in step.depends_on:
            extra.append(po_ref)
    elif step.resource_type == "transition_ledger_transaction":
        lt_ref = step.payload.get("ledger_transaction_id", "")
        if lt_ref.startswith("$ref:") and lt_ref not in step.depends_on:
            extra.append(lt_ref)

    if not extra:
        return step

    return dataclasses.replace(
        step,
        depends_on=step.depends_on + tuple(extra),
    )
