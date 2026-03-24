"""Compile-time diagnostics and status utilities."""

from __future__ import annotations

from .ir import FlowIR


def compute_flow_status(flow_ir: FlowIR) -> str:
    """Compute aggregate flow status from FlowIR at compile time.

    Returns "preview" for compile-time data; post-execution should query
    actual LT statuses from RunManifest.
    """
    return "preview"


def flow_account_deltas(flow_ir: FlowIR) -> dict[str, int]:
    """Compute net balance delta per ledger account for one flow instance.

    Positive = net debit, negative = net credit.
    """
    deltas: dict[str, int] = {}
    for step in flow_ir.steps:
        for lg in step.ledger_groups:
            for entry in lg.entries:
                acct = entry.get("ledger_account_id", "")
                amount = entry.get("amount", 0)
                direction = entry.get("direction", "")
                signed = amount if direction == "debit" else -amount
                deltas[acct] = deltas.get(acct, 0) + signed
    return deltas


def compile_diagnostics(flow_irs: list[FlowIR]) -> dict:
    """Compute compile-time diagnostics across all FlowIR instances."""
    type_counts: dict[str, int] = {}
    trace_values: set[str] = set()
    total_steps = 0
    total_entries = 0
    flow_metadata_keys: set[str] = set()

    for ir in flow_irs:
        trace_values.add(ir.trace_value)
        for step in ir.steps:
            total_steps += 1
            type_counts[step.resource_type] = type_counts.get(step.resource_type, 0) + 1
            for lg in step.ledger_groups:
                total_entries += len(lg.entries)
            for k in step.trace_metadata:
                if k.startswith("_flow_"):
                    flow_metadata_keys.add(k)

    return {
        "type_counts": type_counts,
        "total_steps": total_steps,
        "total_entries": total_entries,
        "trace_values": sorted(trace_values)[:20],
        "trace_value_count": len(trace_values),
        "flow_metadata_keys": sorted(flow_metadata_keys),
    }
