"""Shared rendering and formatting helpers for the Dataloader UI.

Pure functions used by multiple routers — no routes live here.
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from org import DiscoveryResult, _le_display_name
from flow_compiler import actor_display_name, compute_flow_status, flatten_actor_refs
from flow_views import compute_view_data
from handlers import DELETABILITY
from models import DataLoaderConfig, DisplayPhase

# ---------------------------------------------------------------------------
# Templates reference — set by main.py at import time
# ---------------------------------------------------------------------------

_templates: Jinja2Templates | None = None


def set_templates(t: Jinja2Templates) -> None:
    """Called once from the app factory to inject the shared templates instance."""
    global _templates
    _templates = t


def get_templates() -> Jinja2Templates:
    """Return the shared Jinja2Templates instance."""
    assert _templates is not None, "helpers.set_templates() was never called"
    return _templates


# ---------------------------------------------------------------------------
# Validation error formatting
# ---------------------------------------------------------------------------


def format_validation_errors(exc: ValidationError) -> list[dict]:
    """Transform Pydantic ValidationError into LLM-readable structured list."""
    errors = []
    for err in exc.errors():
        path = _format_loc(err["loc"])
        errors.append({
            "path": path,
            "type": err["type"],
            "message": err["msg"],
        })
    return errors


def _format_loc(loc: tuple) -> str:
    """Join Pydantic loc tuple into a dotted path with array indices."""
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{item}]"
            else:
                parts.append(str(item))
        else:
            parts.append(str(item))
    return ".".join(parts)


# ---------------------------------------------------------------------------
# Error response helpers
# ---------------------------------------------------------------------------


def error_html(title: str, detail: str) -> str:
    """Render an error alert partial."""
    return get_templates().get_template("partials/error_alert.html").render(
        title=title, detail=detail
    )


def error_response(title: str, detail: str, status_code: int = 200) -> HTMLResponse:
    """Return error as HTML partial. Default 200 so HTMX swaps the content
    inline (base.html htmx:beforeSwap handles 4xx/5xx differently)."""
    return HTMLResponse(content=error_html(title, detail), status_code=status_code)


# ---------------------------------------------------------------------------
# Display name extraction
# ---------------------------------------------------------------------------

_NAME_ATTRS = {
    "connection":       ("nickname",),
    "counterparty":     ("name",),
    "external_account": ("party_name",),
    "internal_account": ("name",),
    "virtual_account":  ("name",),
    "ledger":           ("name",),
    "ledger_account":   ("name",),
    "ledger_account_category": ("name",),
    "payment_order":    ("description",),
    "expected_payment": ("description",),
    "incoming_payment_detail": ("description",),
    "ledger_transaction": ("description",),
    "return":           ("reason",),
}


def extract_display_name(resource: Any) -> str:
    """Pull a human-meaningful label from a resource config.

    Falls back to first_name + last_name for legal entities, then empty string.
    """
    rt = getattr(resource, "resource_type", "")
    attrs = _NAME_ATTRS.get(rt)
    if attrs:
        for attr in attrs:
            val = getattr(resource, attr, None)
            if val:
                return str(val)

    if rt == "legal_entity":
        le_type = getattr(resource, "legal_entity_type", "")
        if le_type == "business":
            bname = getattr(resource, "business_name", None)
            if bname:
                return str(bname)
        first = getattr(resource, "first_name", "") or ""
        last = getattr(resource, "last_name", "") or ""
        full = f"{first} {last}".strip()
        if full:
            return full

    return ""


# ---------------------------------------------------------------------------
# Preview builder
# ---------------------------------------------------------------------------


def build_preview(
    batches: list[list[str]],
    resource_map: dict[str, Any],
) -> list[dict]:
    """Transform DAG batches into template-friendly preview data."""
    from engine import extract_ref_dependencies

    items: list[dict] = []
    for batch_idx, batch in enumerate(batches):
        for ref in batch:
            resource = resource_map[ref]
            meta = getattr(resource, "metadata", {})
            sandbox_info = extract_sandbox_info(resource)
            items.append(
                {
                    "typed_ref": ref,
                    "resource_type": resource.resource_type,
                    "display_phase": resource.display_phase,
                    "display_name": extract_display_name(resource),
                    "batch": batch_idx,
                    "deletable": DELETABILITY.get(resource.resource_type, False),
                    "has_metadata": bool(meta),
                    "metadata": meta,
                    "deps": list(extract_ref_dependencies(resource))
                    + [d[5:] for d in getattr(resource, "depends_on", []) if d.startswith("$ref:")],
                    "sandbox_info": sandbox_info,
                }
            )
    return items


_INFRA_RESOURCE_TYPES: frozenset[str] = frozenset({
    "connection",
    "legal_entity",
    "counterparty",
    "internal_account",
    "external_account",
    "virtual_account",
    "ledger",
    "ledger_account",
    "ledger_account_category",
    "category_membership",
    "nested_category",
})


def build_flow_grouped_preview(session: Any) -> list[dict]:
    """Build flow-grouped preview data for the flow-aware preview page.

    Each flow instance gets a block containing only money-movement steps
    (POs, IPDs, EPs, LTs, returns, reversals, TLTs).  All infrastructure
    (accounts, CPs, LEs, connections, ledgers) is collected into a single
    shared "Infrastructure" group at the top.
    """
    orig_flows = session.expanded_flows or []
    flow_irs = session.flow_ir or []
    all_items = session.preview_items or []

    claimed_refs: set[str] = set()
    infra_bucket: list[dict] = []
    groups: list[dict] = []

    for i, ir in enumerate(flow_irs):
        fc = orig_flows[i] if i < len(orig_flows) else None

        # Build the set of refs that this flow instance emitted
        flow_step_refs: set[str] = set()
        for s in ir.steps:
            flow_step_refs.add(f"{s.resource_type}.{s.emitted_ref}")
            for lg in s.ledger_groups:
                if not lg.inline:
                    flow_step_refs.add(
                        f"ledger_transaction.{s.emitted_ref}__{lg.group_id}"
                    )

        instance_prefix = f"{ir.flow_ref}__{ir.instance_id}"

        # Classify every item that belongs to this flow instance
        flow_items: list[dict] = []
        for item in all_items:
            if item["typed_ref"] in claimed_refs:
                continue
            belongs = (
                item["typed_ref"] in flow_step_refs
                or item.get("metadata", {}).get(ir.trace_key) == ir.trace_value
                or instance_prefix in item["typed_ref"]
            )
            if not belongs:
                continue
            claimed_refs.add(item["typed_ref"])
            if item["resource_type"] in _INFRA_RESOURCE_TYPES:
                infra_bucket.append(item)
            else:
                flow_items.append(item)

        actors_data: list[dict] = []
        if fc:
            flat_actors = flatten_actor_refs(fc.actors)
            for alias, ref in flat_actors.items():
                rt = ref.replace("$ref:", "").split(".")[0] if "$ref:" in ref else ""
                resolved = resolve_resource_display(ref, session.config)
                actors_data.append({
                    "alias": alias,
                    "ref": ref,
                    "resource_type": rt,
                    "display_name": actor_display_name(ref),
                    "resolved_name": resolved,
                    "is_instance": "{instance}" in ref,
                })

        groups.append({
            "flow_ref": ir.flow_ref,
            "pattern_type": ir.pattern_type,
            "trace_key": ir.trace_key,
            "trace_value": ir.trace_value,
            "step_count": len(ir.steps),
            "status": compute_flow_status(ir),
            "actors": actors_data,
            "flow_items": flow_items,
            "total_items": len(flow_items),
            "flow_diagram_idx": i,
        })

    # Everything not claimed by a flow instance is shared infrastructure
    unclaimed = [item for item in all_items if item["typed_ref"] not in claimed_refs]
    all_infra = infra_bucket + unclaimed
    if all_infra:
        type_counts: dict[str, int] = {}
        for item in all_infra:
            rt = item["resource_type"]
            type_counts[rt] = type_counts.get(rt, 0) + 1
        infra_summary = ", ".join(
            f"{c} {t}" for t, c in sorted(type_counts.items())
        )
        groups.insert(0, {
            "flow_ref": "Infrastructure",
            "pattern_type": "shared",
            "trace_key": "",
            "trace_value": "Shared resources",
            "step_count": 0,
            "status": "infra",
            "actors": [],
            "flow_items": [],
            "total_items": len(all_infra),
            "infra_items": all_infra,
            "infra_summary": infra_summary,
            "flow_diagram_idx": -1,
        })

    return groups


def resolve_resource_display(ref: str, config: DataLoaderConfig) -> str:
    """Resolve a $ref to a human-readable display name from compiled config."""
    if not ref.startswith("$ref:"):
        return ref
    cleaned = ref[5:]
    parts = cleaned.split(".", 1)
    if len(parts) < 2:
        return cleaned
    rtype, rref = parts[0], parts[1]

    section_map = {
        "internal_account": "internal_accounts",
        "ledger_account": "ledger_accounts",
        "counterparty": "counterparties",
        "legal_entity": "legal_entities",
        "connection": "connections",
        "ledger": "ledgers",
    }
    section_name = section_map.get(rtype, rtype + "s")
    section = getattr(config, section_name, None) or []
    base_ref = rref.split(".")[0].split("[")[0]
    for resource in section:
        if getattr(resource, "ref", None) == base_ref:
            name = (
                getattr(resource, "name", None)
                or getattr(resource, "business_name", None)
                or getattr(resource, "nickname", None)
                or base_ref
            )
            return name
    return actor_display_name(ref)


def extract_sandbox_info(resource: Any) -> str | None:
    """Return a human-readable sandbox behavior label for counterparties."""
    accounts = getattr(resource, "accounts", None)
    if not accounts:
        return None
    for acct in accounts:
        behavior = getattr(acct, "sandbox_behavior", None)
        if behavior == "success":
            return "sandbox: success"
        elif behavior == "return":
            code = getattr(acct, "sandbox_return_code", None) or "R01"
            return f"sandbox: auto-return {code.upper()}"
        elif behavior == "failure":
            return "sandbox: auto-fail"
    return None


def build_discovered_by_type(
    discovery: DiscoveryResult | None,
) -> dict[str, list[dict]]:
    """Group all discovered resources by type for the remap UI dropdowns."""
    if discovery is None:
        return {}
    result: dict[str, list[dict]] = {}
    for dc in discovery.connections:
        currencies_str = ", ".join(dc.currencies) if dc.currencies else "no IAs"
        result.setdefault("connection", []).append(
            {"id": dc.id, "name": dc.vendor_name, "detail": f"{currencies_str}"}
        )
    for dia in discovery.internal_accounts:
        result.setdefault("internal_account", []).append(
            {"id": dia.id, "name": dia.name or dia.id[:12], "detail": dia.currency}
        )
    for dl in discovery.ledgers:
        result.setdefault("ledger", []).append(
            {"id": dl.id, "name": dl.name, "detail": ""}
        )
    for dla in discovery.ledger_accounts:
        result.setdefault("ledger_account", []).append(
            {"id": dla.id, "name": dla.name, "detail": f"{dla.currency}"}
        )
    for dlac in discovery.ledger_account_categories:
        result.setdefault("ledger_account_category", []).append(
            {"id": dlac.id, "name": dlac.name, "detail": f"{dlac.currency}"}
        )
    for dle in discovery.legal_entities:
        result.setdefault("legal_entity", []).append(
            {"id": dle.id, "name": _le_display_name(dle), "detail": f"status={dle.status}"}
        )
    for dcp in discovery.counterparties:
        result.setdefault("counterparty", []).append(
            {"id": dcp.id, "name": dcp.name, "detail": f"{dcp.account_count} accounts"}
        )
    return result


def build_discovered_id_lookup(
    discovery: DiscoveryResult,
) -> dict[str, dict]:
    """Build a flat ID → info lookup across all discovered resource types."""
    lookup: dict[str, dict] = {}
    for dc in discovery.connections:
        lookup[dc.id] = {"name": dc.vendor_name, "type": "connection"}
    for dia in discovery.internal_accounts:
        lookup[dia.id] = {"name": dia.name or dia.id[:12], "type": "internal_account"}
    for dl in discovery.ledgers:
        lookup[dl.id] = {"name": dl.name, "type": "ledger"}
    for dla in discovery.ledger_accounts:
        lookup[dla.id] = {"name": dla.name, "type": "ledger_account"}
    for dlac in discovery.ledger_account_categories:
        lookup[dlac.id] = {"name": dlac.name, "type": "ledger_account_category"}
    for dle in discovery.legal_entities:
        lookup[dle.id] = {"name": _le_display_name(dle), "type": "legal_entity"}
    for dcp in discovery.counterparties:
        lookup[dcp.id] = {"name": dcp.name, "type": "counterparty"}
    return lookup


# ---------------------------------------------------------------------------
# Flow view helpers (used by flows router)
# ---------------------------------------------------------------------------


def get_flow_view_data(session: Any, flow_idx: int):
    """Return (flow_ir, flow_config, view_data) for the given flow index, or None."""
    if not session.flow_ir or flow_idx < 0 or flow_idx >= len(session.flow_ir):
        return None
    flow_ir = session.flow_ir[flow_idx]
    orig_flows = session.expanded_flows or []
    flow_config = orig_flows[flow_idx] if flow_idx < len(orig_flows) else None

    if session.view_data_cache and flow_idx < len(session.view_data_cache):
        view_data = session.view_data_cache[flow_idx]
    elif flow_config:
        view_data = compute_view_data(flow_ir, flow_config)
    else:
        from flow_views import FlowViewData
        view_data = FlowViewData()
    return flow_ir, flow_config, view_data


def fmt_amt(amt) -> str:
    """Format a cents amount as dollars."""
    if isinstance(amt, (int, float)):
        return f"${amt / 100:,.2f}"
    return str(amt)


SOURCE_BADGE = {
    "payment_order": "PO",
    "incoming_payment_detail": "IPD",
    "expected_payment": "EP",
    "return": "Ret",
    "reversal": "Rev",
}
