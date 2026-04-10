"""Per-step DSL → ``FlowIRStep`` compilation (ledger groups, actors, lifecycle)."""

from __future__ import annotations

from typing import Any

from models import DataLoaderConfig, FundsFlowConfig, PaymentOrderStep, _StepBase

from .core_emit import _validate_account_roles
from .core_lifecycle import (
    _auto_derive_lifecycle_refs,
    _find_reverse_target,
    _flip_entry,
)
from .ir import FlowIRStep, LedgerGroup

_DSL_ONLY_FIELDS = frozenset(
    {
        "step_id",
        "type",
        "depends_on",
        "ledger_entries",
        "ledger_status",
        "ledger_inline",
        "staged",
        "fulfills",
    }
)


def _internal_account_ref_key(ref: str) -> str | None:
    """Return ``internal_account.<ref>`` key if *ref* targets an internal account."""
    prefix = "$ref:internal_account."
    if not ref.startswith(prefix):
        return None
    tail = ref[len(prefix) :]
    if not tail:
        return None
    base = tail.split(".", 1)[0]
    return f"internal_account.{base}"


def _ledger_account_ref_for_internal(internal_account_ref: str) -> str:
    """``$ref:internal_account.X`` → ``$ref:internal_account.X.ledger_account`` (MT child)."""
    if not internal_account_ref.startswith("$ref:internal_account."):
        raise ValueError(f"Expected internal account ref, got {internal_account_ref!r}")
    body = internal_account_ref.removeprefix("$ref:")
    return f"$ref:{body}.ledger_account"


def _internal_account_currency(base: DataLoaderConfig, ia_key: str) -> str | None:
    """Resolve ``internal_account.<ref>`` to configured currency."""
    suffix = ia_key.removeprefix("internal_account.")
    for ia in base.internal_accounts:
        if ia.ref == suffix:
            return ia.currency
    return None


def _cross_currency_internal_book(
    base: DataLoaderConfig,
    originating_account_id: str,
    receiving_account_id: str,
) -> bool:
    """True when both legs are internal accounts with different currencies (e.g. USD↔USDC)."""
    o_key = _internal_account_ref_key(originating_account_id)
    r_key = _internal_account_ref_key(receiving_account_id)
    if not o_key or not r_key:
        return False
    co = _internal_account_currency(base, o_key)
    cr = _internal_account_currency(base, r_key)
    if not co or not cr:
        return False
    return co != cr


def _resolve_actors(obj: Any, actor_refs: dict[str, str]) -> Any:
    """Delegate to ``core.resolve_actors`` (lazy import avoids core ↔ step_compile cycle)."""
    import flow_compiler.core as core_module

    return core_module.resolve_actors(obj, actor_refs)


def _compile_step(
    step: _StepBase,
    flow: FundsFlowConfig,
    instance_id: str,
    trace_meta: dict[str, str],
    step_ref_map: dict[str, str],
    all_steps: list[_StepBase],
    actor_refs: dict[str, str],
    og_step_ids: dict[str, str],
    base_config: DataLoaderConfig,
) -> FlowIRStep:
    """Compile a single DSL step into a FlowIRStep."""
    exclude = _DSL_ONLY_FIELDS & set(type(step).model_fields)
    step_dict = step.model_dump(exclude=exclude, exclude_none=True)

    if "payment_type" in step_dict:
        step_dict["type"] = step_dict.pop("payment_type")

    step_dict = _resolve_actors(step_dict, actor_refs)
    _validate_account_roles(step, step_dict, flow.ref)

    step_dict["metadata"] = {
        **step_dict.get("metadata", {}),
        **trace_meta,
    }

    if getattr(step, "staged", False):
        step_dict["staged"] = True

    ir_depends = [step_ref_map[dep] for dep in step.depends_on if dep in step_ref_map]

    _auto_derive_lifecycle_refs(step, step_dict, step_ref_map, all_steps)

    ledger_groups: list[LedgerGroup] = []
    entries = getattr(step, "ledger_entries", None)
    if entries == "reverse_parent":
        parent = _find_reverse_target(step, all_steps)
        if parent:
            parent_entries = getattr(parent, "ledger_entries", None)
            if isinstance(parent_entries, list) and parent_entries:
                reversed_entries = _resolve_actors(
                    [_flip_entry(e.model_dump(exclude_none=True)) for e in parent_entries],
                    actor_refs,
                )
                inline = getattr(step, "ledger_inline", False)
                status = getattr(step, "ledger_status", None)
                ledger_groups.append(
                    LedgerGroup(
                        group_id=f"{step.step_id}_lg0",
                        inline=inline,
                        entries=tuple(reversed_entries),
                        metadata=trace_meta.copy(),
                        status=status,
                    )
                )
    elif isinstance(entries, list) and entries:
        entries_resolved = _resolve_actors(
            [e.model_dump(exclude_none=True) for e in entries],
            actor_refs,
        )
        inline = getattr(step, "ledger_inline", False)
        status = getattr(step, "ledger_status", None)
        ledger_groups.append(
            LedgerGroup(
                group_id=f"{step.step_id}_lg0",
                inline=inline,
                entries=tuple(entries_resolved),
                metadata=trace_meta.copy(),
                status=status,
            )
        )
    elif (
        isinstance(step, PaymentOrderStep)
        and step_dict.get("type") == "book"
        and entries is None
        and isinstance(step_dict.get("originating_account_id"), str)
        and isinstance(step_dict.get("receiving_account_id"), str)
        and _cross_currency_internal_book(
            base_config,
            step_dict["originating_account_id"],
            step_dict["receiving_account_id"],
        )
    ):
        # MT auto-LT for cross-currency book uses mixed unit semantics; emit an explicit
        # balanced inline LT with the same minor-unit amount on both legs (USD-style cents
        # for USD and USDC — Tradeify / stablecoin ramp).
        amt = int(step_dict.get("amount") or 0)
        if amt > 0:
            orig_ref = step_dict["originating_account_id"]
            recv_ref = step_dict["receiving_account_id"]
            la_orig = _ledger_account_ref_for_internal(orig_ref)
            la_recv = _ledger_account_ref_for_internal(recv_ref)
            direction = step_dict.get("direction", "credit")
            if direction == "credit":
                debit_la, credit_la = la_recv, la_orig
            else:
                debit_la, credit_la = la_orig, la_recv
            auto_entries = (
                {"amount": amt, "direction": "debit", "ledger_account_id": debit_la},
                {"amount": amt, "direction": "credit", "ledger_account_id": credit_la},
            )
            ledger_groups.append(
                LedgerGroup(
                    group_id=f"{step.step_id}_lg_auto_xcur_book",
                    inline=True,
                    entries=auto_entries,
                    metadata=trace_meta.copy(),
                    status=getattr(step, "ledger_status", None),
                )
            )

    return FlowIRStep(
        step_id=step.step_id,
        flow_ref=flow.ref,
        instance_id=instance_id,
        depends_on=tuple(ir_depends),
        resource_type=step.type,
        payload=step_dict,
        ledger_groups=tuple(ledger_groups),
        trace_metadata=trace_meta,
        optional_group=og_step_ids.get(step.step_id),
    )
