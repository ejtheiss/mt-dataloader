"""FlowIR dataclasses and StepRelationships index.

Lowest-level module in the flow_compiler package — no internal
cross-imports.  Everything here is either a frozen dataclass or a
pure builder function.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from models import (
    IncomingPaymentDetailStep,
    OptionalGroupConfig,
    ReturnStep,
    ReversalStep,
    TransitionLedgerTransactionStep,
    _extract_step_ref,
    _StepBase,
)

# ---------------------------------------------------------------------------
# FlowIR dataclasses (internal — not Pydantic)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerGroup:
    """One set of ledger entries that emits as a standalone LT or inline LT."""

    group_id: str
    inline: bool
    entries: tuple[dict, ...]
    metadata: dict[str, str]
    status: str | None = None


@dataclass(frozen=True)
class FlowIRStep:
    """One step in the FlowIR — compiles to one resource in DataLoaderConfig."""

    step_id: str
    flow_ref: str
    instance_id: str
    depends_on: tuple[str, ...]
    resource_type: str
    payload: dict
    ledger_groups: tuple[LedgerGroup, ...]
    trace_metadata: dict[str, str]
    optional_group: str | None = None
    preview_only: bool = False

    @property
    def emitted_ref(self) -> str:
        return f"{self.flow_ref}__{self.instance_id}__{self.step_id}"


@dataclass(frozen=True)
class FlowIR:
    """Complete IR for one flow instance."""

    flow_ref: str
    instance_id: str
    pattern_type: str
    trace_key: str
    trace_value: str
    trace_metadata: dict[str, str]
    steps: tuple[FlowIRStep, ...] = ()


# ---------------------------------------------------------------------------
# StepRelationships — resolved cross-step index (computed once per flow)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StepRelationships:
    """Resolved cross-step relationships. Computed once per flow,
    used by all downstream passes.

    Eliminates redundant O(n) scans: five independent locations previously
    re-derived parent-child and fulfillment relationships by walking the
    flat step list.  This index provides O(1) lookups instead.
    """

    lifecycle_parent: dict[str, str]
    lifecycle_children: dict[str, tuple[str, ...]]

    fulfills: dict[str, str]
    fulfilled_by: dict[str, tuple[str, ...]]

    dependency_graph: dict[str, tuple[str, ...]]
    step_by_id: dict[str, _StepBase]
    optional_group: dict[str, str]


def build_step_relationships(
    steps: Sequence[_StepBase],
    optional_groups: Sequence[OptionalGroupConfig] | None = None,
) -> StepRelationships:
    """Single O(n) walk of the step list → frozen relationship index.

    Called once per flow after optional-group flattening.  For the
    non-flattened case (preview rendering), pass *optional_groups*
    to populate the optional_group map from group definitions.
    """
    step_by_id: dict[str, _StepBase] = {s.step_id: s for s in steps}

    og_map: dict[str, str] = {}
    if optional_groups:
        for og in optional_groups:
            for s in og.steps:
                og_map[s.step_id] = og.label
    for s in steps:
        if "_flow_optional_group" in s.metadata and s.step_id not in og_map:
            og_map[s.step_id] = s.metadata["_flow_optional_group"]

    dep_graph: dict[str, tuple[str, ...]] = {s.step_id: tuple(s.depends_on) for s in steps}

    lifecycle_parent: dict[str, str] = {}
    fulfills_map: dict[str, str] = {}

    for step in steps:
        if isinstance(step, ReturnStep) and step.returnable_id:
            target = _extract_step_ref(step.returnable_id)
            if target and target in step_by_id:
                lifecycle_parent[step.step_id] = target

        if isinstance(step, ReversalStep) and step.payment_order_id:
            target = _extract_step_ref(step.payment_order_id)
            if target and target in step_by_id:
                lifecycle_parent[step.step_id] = target

        if isinstance(step, TransitionLedgerTransactionStep) and step.ledger_transaction_id:
            target = _extract_step_ref(step.ledger_transaction_id)
            if target and target in step_by_id:
                lifecycle_parent[step.step_id] = target

        if isinstance(step, IncomingPaymentDetailStep) and step.fulfills:
            target = _extract_step_ref(step.fulfills)
            if target and target in step_by_id:
                fulfills_map[step.step_id] = target

    lc_children: dict[str, list[str]] = {}
    for child, parent in lifecycle_parent.items():
        lc_children.setdefault(parent, []).append(child)

    fb_map: dict[str, list[str]] = {}
    for ipd_id, ep_id in fulfills_map.items():
        fb_map.setdefault(ep_id, []).append(ipd_id)

    return StepRelationships(
        lifecycle_parent=lifecycle_parent,
        lifecycle_children={k: tuple(v) for k, v in lc_children.items()},
        fulfills=fulfills_map,
        fulfilled_by={k: tuple(v) for k, v in fb_map.items()},
        dependency_graph=dep_graph,
        step_by_id=step_by_id,
        optional_group=og_map,
    )


# ---------------------------------------------------------------------------
# Account-type classification (shared by Mermaid, views, and validators)
# ---------------------------------------------------------------------------

_REF_PREFIX_TO_ACCOUNT_TYPE: dict[str, str] = {
    "internal_account": "internal_account",
    "external_account": "external_account",
    "counterparty": "external_account",
    "ledger_account": "ledger_account",
    "virtual_account": "virtual_account",
}


def _ref_account_type(ref: str) -> str:
    """Classify a ``$ref:`` string by its prefix.

    ``$ref:internal_account.ops_usd`` → ``"internal_account"``
    ``$ref:counterparty.cust.account[0]`` → ``"external_account"``
    """
    stripped = ref.replace("$ref:", "")
    prefix = stripped.split(".")[0]
    return _REF_PREFIX_TO_ACCOUNT_TYPE.get(prefix, "unknown")
