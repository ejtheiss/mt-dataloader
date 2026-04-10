"""Emit FlowIR into ``DataLoaderConfig`` resource sections."""

from __future__ import annotations

from typing import Any

from models import RESOURCE_TYPE_TO_SECTION, DataLoaderConfig, _StepBase

from .core_lifecycle import _with_lifecycle_depends_on
from .ir import FlowIR

_EXTERNAL_ACCOUNT_PREFIXES = frozenset({"counterparty", "external_account"})
_INTERNAL_ACCOUNT_PREFIXES = frozenset({"internal_account"})


def _validate_account_roles(
    step: _StepBase,
    resolved: dict[str, Any],
    flow_ref: str,
) -> None:
    """Validate that account refs are appropriate for the step type."""
    orig = resolved.get("originating_account_id", "")
    if not orig:
        return

    prefix = orig.replace("$ref:", "").split(".")[0]

    if step.type == "payment_order":
        if prefix in _EXTERNAL_ACCOUNT_PREFIXES:
            raise ValueError(
                f"Flow '{flow_ref}', step '{step.step_id}': "
                f"originating_account_id must be an internal account "
                f"(got '{orig}' which is a {prefix}). "
                f"The ODFI for a payment order is always a platform IA."
            )
    elif step.type in ("incoming_payment_detail", "expected_payment"):
        if prefix in _INTERNAL_ACCOUNT_PREFIXES:
            raise ValueError(
                f"Flow '{flow_ref}', step '{step.step_id}': "
                f"originating_account_id on an IPD/EP must be an "
                f"external account — the ODFI is the sender's EA "
                f"(got '{orig}' which is an internal_account)."
            )


def emit_dataloader_config(
    flow_irs: list[FlowIR],
    base_config: DataLoaderConfig,
    extra_resources: dict[str, list[dict]] | None = None,
) -> DataLoaderConfig:
    """Emit FlowIR steps into DataLoaderConfig resource sections."""
    data = base_config.model_dump(exclude_none=True)
    data["funds_flows"] = []

    if extra_resources:
        for section, items in extra_resources.items():
            existing = data.get(section, [])
            existing_refs = {item.get("ref") for item in existing if isinstance(item, dict)}
            for item in items:
                if isinstance(item, dict) and item.get("ref") in existing_refs:
                    continue
                existing.append(item)
                if isinstance(item, dict) and item.get("ref"):
                    existing_refs.add(item["ref"])
            data[section] = existing

    for flow_ir in flow_irs:
        for step in flow_ir.steps:
            if step.preview_only:
                continue
            step = _with_lifecycle_depends_on(step)

            ref = step.emitted_ref
            resource_type = step.resource_type
            section = RESOURCE_TYPE_TO_SECTION[resource_type]

            resource_dict: dict[str, Any] = {
                "ref": ref,
                **step.payload,
            }
            if resource_type in ("incoming_payment_detail", "expected_payment"):
                resource_dict.pop("originating_account_id", None)
            if resource_type in ("return", "reversal", "transition_ledger_transaction"):
                resource_dict.pop("description", None)
            if resource_type in (
                "verify_external_account",
                "complete_verification",
                "archive_resource",
            ):
                # DSL authoring fields — not on VerifyExternalAccountConfig /
                # CompleteVerificationConfig / ArchiveResourceConfig; keep them on
                # FlowIR payload for Mermaid labels only.
                resource_dict.pop("description", None)
                resource_dict.pop("timing", None)
            if step.depends_on:
                resource_dict["depends_on"] = step.depends_on

            for lg in step.ledger_groups:
                if not lg.inline:
                    if resource_type == "ledger_transaction":
                        resource_dict["ledger_entries"] = lg.entries
                        resource_dict["metadata"] = {
                            **resource_dict.get("metadata", {}),
                            **lg.metadata,
                        }
                        if lg.status:
                            resource_dict["status"] = lg.status
                    else:
                        lt_ref = f"{ref}__{lg.group_id}"
                        parent_typed_ref = f"$ref:{resource_type}.{ref}"
                        lt_dict: dict[str, Any] = {
                            "ref": lt_ref,
                            "ledger_entries": lg.entries,
                            "metadata": lg.metadata,
                            "depends_on": [parent_typed_ref],
                            "ledgerable_type": resource_type,
                            "ledgerable_id": parent_typed_ref,
                        }
                        if step.payload.get("description"):
                            lt_dict["description"] = step.payload["description"]
                        if lg.status:
                            lt_dict["status"] = lg.status
                        data.setdefault("ledger_transactions", []).append(lt_dict)
                else:
                    inline_lt: dict[str, Any] = {
                        "ledger_entries": lg.entries,
                        "metadata": lg.metadata,
                    }
                    if step.payload.get("description"):
                        inline_lt["description"] = step.payload["description"]
                    if lg.status:
                        inline_lt["status"] = lg.status
                    resource_dict["ledger_transaction"] = inline_lt

            data.setdefault(section, []).append(resource_dict)

    return DataLoaderConfig.model_validate(data)
