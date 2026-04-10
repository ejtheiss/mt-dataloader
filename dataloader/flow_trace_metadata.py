"""Trace vs per-step metadata — shared rules for compiler and Fund Flows UI.

``flow_compiler.core._compile_step`` merges:

    payload["metadata"] = {**step.metadata, **trace_meta}

where ``trace_meta`` is ``{flow.trace_key: expanded_value, **flow.trace_metadata}``.
Flow-level keys therefore **win** on collision; duplicating them on steps is redundant.
"""

from __future__ import annotations


def step_only_metadata(flow_trace: dict[str, str], step_payload: dict) -> dict[str, str]:
    """Metadata keys authored on the step only (exclude flow-wide trace stamp)."""
    merged = dict(step_payload.get("metadata") or {})
    flow_keys = set(flow_trace.keys())
    out: dict[str, str] = {}
    for k, v in merged.items():
        if k in flow_keys or str(k).startswith("_flow_"):
            continue
        out[str(k)] = v if isinstance(v, str) else str(v)
    return out


def forbidden_trace_keys(
    trace_key: str | None,
    trace_metadata: dict[str, str] | None,
) -> set[str]:
    keys = set(trace_metadata or {})
    if trace_key:
        keys.add(trace_key)
    return keys
