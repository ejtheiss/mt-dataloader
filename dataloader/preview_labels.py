"""Flow-grouped preview: assign ``preview_items`` to infrastructure vs per-flow groups."""

from __future__ import annotations

from typing import Any

from dataloader.engine import all_resources, typed_ref_for
from dataloader.engine.resource_display import extract_display_name
from flow_compiler import actor_display_name, compute_flow_status, flatten_actor_refs
from models import DataLoaderConfig


def dollar_ref_body(ref: str) -> str | None:
    if not ref.startswith("$ref:"):
        return None
    body = ref[5:]
    if "." not in body:
        return None
    return body


def typed_ref_lookup_variants(typed: str) -> list[str]:
    """Longest-first: ``counterparty.vendor.account[0]`` → parent ``counterparty.vendor``."""
    parts = typed.split(".")
    if len(parts) < 2:
        return [typed] if typed else []
    return [".".join(parts[:end]) for end in range(len(parts), 1, -1)]


def display_label_from_resource(
    resource: Any,
    ref: str,
    resource_map: dict[str, Any],
) -> str:
    label = extract_display_name(resource)
    if label:
        return label
    name = (
        getattr(resource, "name", None)
        or getattr(resource, "business_name", None)
        or getattr(resource, "nickname", None)
    )
    if name:
        return str(name)
    if getattr(resource, "resource_type", "") == "external_account":
        cp_ref = getattr(resource, "counterparty_id", None)
        if isinstance(cp_ref, str) and cp_ref.startswith("$ref:"):
            cp_typed = cp_ref.removeprefix("$ref:")
            other = resource_map.get(cp_typed)
            if other is not None:
                cp_label = extract_display_name(other)
                if cp_label:
                    return cp_label
    return actor_display_name(ref)


def resolve_resource_display(ref: str, config: DataLoaderConfig) -> str:
    if not ref.startswith("$ref:"):
        return ref
    body = dollar_ref_body(ref)
    if not body:
        return ref[5:]
    resource_map = {typed_ref_for(r): r for r in all_resources(config)}
    for t in typed_ref_lookup_variants(body):
        resource = resource_map.get(t)
        if resource is not None:
            return display_label_from_resource(resource, ref, resource_map)
    return actor_display_name(ref)


_INFRA_RESOURCE_TYPES: frozenset[str] = frozenset(
    {
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
    }
)


def build_flow_grouped_preview(session: Any) -> list[dict]:
    orig_flows = session.expanded_flows or []
    flow_irs = session.flow_ir or []
    all_items = session.preview_items or []

    claimed_refs: set[str] = set()
    infra_bucket: list[dict] = []
    groups: list[dict] = []

    for i, ir in enumerate(flow_irs):
        fc = orig_flows[i] if i < len(orig_flows) else None

        flow_step_refs: set[str] = set()
        for s in ir.steps:
            flow_step_refs.add(f"{s.resource_type}.{s.emitted_ref}")
            for lg in s.ledger_groups:
                if not lg.inline:
                    flow_step_refs.add(f"ledger_transaction.{s.emitted_ref}__{lg.group_id}")

        instance_prefix = f"{ir.flow_ref}__{ir.instance_id}"

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
                actors_data.append(
                    {
                        "alias": alias,
                        "ref": ref,
                        "resource_type": rt,
                        "is_instance": "{instance}" in ref,
                    }
                )

        groups.append(
            {
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
            }
        )

    unclaimed = [item for item in all_items if item["typed_ref"] not in claimed_refs]
    all_infra = infra_bucket + unclaimed
    if all_infra:
        type_counts: dict[str, int] = {}
        for item in all_infra:
            rt = item["resource_type"]
            type_counts[rt] = type_counts.get(rt, 0) + 1
        infra_summary = ", ".join(f"{c} {t}" for t, c in sorted(type_counts.items()))
        groups.insert(
            0,
            {
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
            },
        )

    return groups
