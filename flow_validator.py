"""Flow validation rules — advisory diagnostics from compiled FlowIR.

Rules are pluggable: implement FlowRule.check() and append to DEFAULT_RULES.
Run after compile_flows() to surface warnings/info that aren't parse errors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from flow_compiler import FlowIR

__all__ = [
    "FlowDiagnostic",
    "FlowRule",
    "FlowValidationContext",
    "FlowValidator",
    "DEFAULT_RULES",
    "validate_flow",
]


@dataclass(frozen=True)
class FlowDiagnostic:
    """One validation finding."""

    rule_id: str
    severity: Literal["error", "warning", "info"]
    step_id: str | None
    account_id: str | None
    message: str


@dataclass
class FlowValidationContext:
    """Pre-computed cross-step context for rule evaluation."""

    account_net_positions: dict[str, int] = field(default_factory=dict)
    step_sequence: list[str] = field(default_factory=list)
    actor_account_map: dict[str, str] = field(default_factory=dict)
    payment_types_by_step: dict[str, str] = field(default_factory=dict)
    steps_with_returns: set[str] = field(default_factory=set)
    steps_with_reversals: set[str] = field(default_factory=set)
    actor_refs: set[str] = field(default_factory=set)


class FlowRule(ABC):
    rule_id: str
    severity: Literal["error", "warning", "info"]
    description: str

    @abstractmethod
    def check(self, flow: FlowIR, ctx: FlowValidationContext) -> list[FlowDiagnostic]:
        ...


def _build_context(flow: FlowIR, actor_refs: dict[str, str] | None = None) -> FlowValidationContext:
    """Build validation context from a compiled FlowIR.

    ``actor_refs`` is a flat ``frame.slot → $ref:`` mapping (from
    ``flatten_actor_refs``).
    """
    ctx = FlowValidationContext()
    ctx.actor_account_map = {ref: key for key, ref in (actor_refs or {}).items()}
    ctx.actor_refs = set((actor_refs or {}).values())

    for step in flow.steps:
        ctx.step_sequence.append(step.step_id)
        payload = step.payload
        rtype = step.resource_type

        if rtype == "payment_order":
            ctx.payment_types_by_step[step.step_id] = payload.get("type", "") or payload.get("payment_type", "")

        if rtype in ("return",):
            for dep_ref in step.depends_on:
                parts = dep_ref.rsplit("__", 1)
                if parts:
                    ctx.steps_with_returns.add(parts[-1])

        if rtype in ("reversal",):
            for dep_ref in step.depends_on:
                parts = dep_ref.rsplit("__", 1)
                if parts:
                    ctx.steps_with_reversals.add(parts[-1])

        for lg in step.ledger_groups:
            for entry in lg.entries:
                acct = entry.get("ledger_account_id", "")
                amt = entry.get("amount", 0)
                direction = entry.get("direction", "")
                if acct and isinstance(amt, (int, float)):
                    sign = 1 if direction == "debit" else -1
                    ctx.account_net_positions[acct] = ctx.account_net_positions.get(acct, 0) + sign * amt

    return ctx


# ---------------------------------------------------------------------------
# Built-in rules
# ---------------------------------------------------------------------------


class LedgerBalanceRule(FlowRule):
    """LEDGER_001: Step-level debit/credit imbalance."""

    rule_id = "LEDGER_001"
    severity: Literal["error", "warning", "info"] = "error"
    description = "Step-level debit/credit imbalance"

    def check(self, flow: FlowIR, ctx: FlowValidationContext) -> list[FlowDiagnostic]:
        diags: list[FlowDiagnostic] = []
        for step in flow.steps:
            for lg in step.ledger_groups:
                total_debit = sum(e.get("amount", 0) for e in lg.entries if e.get("direction") == "debit")
                total_credit = sum(e.get("amount", 0) for e in lg.entries if e.get("direction") == "credit")
                if total_debit != total_credit:
                    diags.append(FlowDiagnostic(
                        rule_id=self.rule_id, severity=self.severity,
                        step_id=step.step_id, account_id=None,
                        message=f"Debit ({total_debit}) ≠ Credit ({total_credit}) in ledger group {lg.group_id}",
                    ))
        return diags


class SelfDebitRule(FlowRule):
    """LEDGER_002: Same account on both sides of one step."""

    rule_id = "LEDGER_002"
    severity: Literal["error", "warning", "info"] = "warning"
    description = "Self-debit — same account on both sides of one step"

    def check(self, flow: FlowIR, ctx: FlowValidationContext) -> list[FlowDiagnostic]:
        diags: list[FlowDiagnostic] = []
        for step in flow.steps:
            for lg in step.ledger_groups:
                debit_accts = {e.get("ledger_account_id") for e in lg.entries if e.get("direction") == "debit"}
                credit_accts = {e.get("ledger_account_id") for e in lg.entries if e.get("direction") == "credit"}
                overlap = debit_accts & credit_accts - {""}
                for acct in overlap:
                    diags.append(FlowDiagnostic(
                        rule_id=self.rule_id, severity=self.severity,
                        step_id=step.step_id, account_id=acct,
                        message=f"Account {acct} appears on both debit and credit sides",
                    ))
        return diags


class NetZeroFlowRule(FlowRule):
    """LEDGER_004: All accounts net to zero (informational)."""

    rule_id = "LEDGER_004"
    severity: Literal["error", "warning", "info"] = "info"
    description = "Net-zero flow — all accounts net to zero"

    def check(self, flow: FlowIR, ctx: FlowValidationContext) -> list[FlowDiagnostic]:
        if not ctx.account_net_positions:
            return []
        if all(v == 0 for v in ctx.account_net_positions.values()):
            return [FlowDiagnostic(
                rule_id=self.rule_id, severity=self.severity,
                step_id=None, account_id=None,
                message="All ledger accounts net to zero across the flow",
            )]
        return []


class OrphanedAccountRule(FlowRule):
    """LEDGER_005: Account in entries but not in actors."""

    rule_id = "LEDGER_005"
    severity: Literal["error", "warning", "info"] = "warning"
    description = "Orphaned account — in entries but not in actors"

    def check(self, flow: FlowIR, ctx: FlowValidationContext) -> list[FlowDiagnostic]:
        if not ctx.actor_refs:
            return []
        diags: list[FlowDiagnostic] = []
        seen: set[str] = set()
        for step in flow.steps:
            for lg in step.ledger_groups:
                for entry in lg.entries:
                    acct = entry.get("ledger_account_id", "")
                    if acct and acct not in ctx.actor_refs and acct not in seen:
                        seen.add(acct)
                        diags.append(FlowDiagnostic(
                            rule_id=self.rule_id, severity=self.severity,
                            step_id=step.step_id, account_id=acct,
                            message=f"Account {acct} used in ledger entries but not declared in actors",
                        ))
        return diags


class RtpIrrevocableRule(FlowRule):
    """PAYMENT_003: RTP PO with return/reversal — RTP is irrevocable."""

    rule_id = "PAYMENT_003"
    severity: Literal["error", "warning", "info"] = "warning"
    description = "RTP PO with return/reversal in the flow — RTP is irrevocable"

    def check(self, flow: FlowIR, ctx: FlowValidationContext) -> list[FlowDiagnostic]:
        diags: list[FlowDiagnostic] = []
        for sid, ptype in ctx.payment_types_by_step.items():
            if ptype == "rtp" and (sid in ctx.steps_with_returns or sid in ctx.steps_with_reversals):
                diags.append(FlowDiagnostic(
                    rule_id=self.rule_id, severity=self.severity,
                    step_id=sid, account_id=None,
                    message=f"RTP payment '{sid}' has return/reversal — RTP is irrevocable",
                ))
        return diags


class EpDeltaRule(FlowRule):
    """PAYMENT_004: EP in account delta — EPs are watchers, not money movement."""

    rule_id = "PAYMENT_004"
    severity: Literal["error", "warning", "info"] = "info"
    description = "EP included in flow — EPs watch for payments, they don't move money"

    def check(self, flow: FlowIR, ctx: FlowValidationContext) -> list[FlowDiagnostic]:
        diags: list[FlowDiagnostic] = []
        for step in flow.steps:
            if step.resource_type == "expected_payment":
                diags.append(FlowDiagnostic(
                    rule_id=self.rule_id, severity=self.severity,
                    step_id=step.step_id, account_id=None,
                    message=f"Expected Payment '{step.step_id}' watches for incoming funds — does not move money",
                ))
        return diags


class TltBackwardLifecycleRule(FlowRule):
    """LIFECYCLE_004: TLT attempts backward lifecycle (posted→pending)."""

    rule_id = "LIFECYCLE_004"
    severity: Literal["error", "warning", "info"] = "warning"
    description = "TLT attempts backward lifecycle transition"

    _VALID_FORWARD = {"pending": {"posted", "archived"}, "posted": {"archived"}}

    def check(self, flow: FlowIR, ctx: FlowValidationContext) -> list[FlowDiagnostic]:
        diags: list[FlowDiagnostic] = []
        for step in flow.steps:
            if step.resource_type != "transition_ledger_transaction":
                continue
            status = step.payload.get("status", "posted")
            parent_status = "pending"
            for dep_ref in step.depends_on:
                parts = dep_ref.rsplit("__", 1)
                dep_id = parts[-1] if parts else ""
                dep_step = next((s for s in flow.steps if s.step_id == dep_id), None)
                if dep_step:
                    parent_status = dep_step.payload.get("ledger_status") or dep_step.payload.get("status") or "pending"
                    break

            valid_targets = self._VALID_FORWARD.get(parent_status, set())
            if status not in valid_targets and parent_status != status:
                diags.append(FlowDiagnostic(
                    rule_id=self.rule_id, severity=self.severity,
                    step_id=step.step_id, account_id=None,
                    message=f"TLT '{step.step_id}' transitions {parent_status}→{status} (not a valid forward lifecycle)",
                ))
        return diags


class ReverseParentNoLtRule(FlowRule):
    """EMBED_003: Return has reverse_parent but parent has no LT."""

    rule_id = "EMBED_003"
    severity: Literal["error", "warning", "info"] = "warning"
    description = "Return has reverse_parent but parent has no ledger entries"

    def check(self, flow: FlowIR, ctx: FlowValidationContext) -> list[FlowDiagnostic]:
        diags: list[FlowDiagnostic] = []
        for step in flow.steps:
            if step.resource_type not in ("return", "reversal"):
                continue
            if not step.ledger_groups:
                for dep_ref in step.depends_on:
                    parts = dep_ref.rsplit("__", 1)
                    dep_id = parts[-1] if parts else ""
                    parent = next((s for s in flow.steps if s.step_id == dep_id), None)
                    if parent and parent.ledger_groups:
                        diags.append(FlowDiagnostic(
                            rule_id=self.rule_id, severity=self.severity,
                            step_id=step.step_id, account_id=None,
                            message=f"'{step.step_id}' depends on '{dep_id}' which has LT, but no reverse_parent entries were generated",
                        ))
        return diags


DEFAULT_RULES: list[FlowRule] = [
    LedgerBalanceRule(),
    SelfDebitRule(),
    NetZeroFlowRule(),
    OrphanedAccountRule(),
    RtpIrrevocableRule(),
    EpDeltaRule(),
    TltBackwardLifecycleRule(),
    ReverseParentNoLtRule(),
]


class FlowValidator:
    """Runs registered rules against a FlowIR and collects diagnostics."""

    def __init__(self, rules: list[FlowRule] | None = None):
        self.rules = list(rules) if rules is not None else list(DEFAULT_RULES)

    def validate(
        self,
        flow: FlowIR,
        actor_refs: dict[str, str] | None = None,
    ) -> list[FlowDiagnostic]:
        ctx = _build_context(flow, actor_refs)
        diags: list[FlowDiagnostic] = []
        for rule in self.rules:
            diags.extend(rule.check(flow, ctx))
        return diags

    def register(self, rule: FlowRule) -> None:
        self.rules.append(rule)


def validate_flow(
    flow: FlowIR,
    actor_refs: dict[str, str] | None = None,
) -> list[FlowDiagnostic]:
    """Convenience function — validate with all default rules."""
    return FlowValidator().validate(flow, actor_refs)
