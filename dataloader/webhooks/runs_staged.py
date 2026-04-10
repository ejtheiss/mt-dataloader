"""Run detail page, staged drawer, and POST fire for staged resources."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from modern_treasury import AsyncModernTreasury
from tenacity import RetryError, retry, retry_if_result

from dataloader.engine import _now_iso
from dataloader.handlers import DELETABILITY, TENACITY_STOP_30, TENACITY_WAIT_EXP_2_10
from dataloader.routers.deps import CurrentAppUserDep, SettingsDep, TemplatesDep
from dataloader.run_access import load_run_manifest_for_reader, run_is_readable
from dataloader.staged_fire import FIREABLE_TYPES
from dataloader.webhooks import correlation_state
from dataloader.webhooks.webhook_persist import (
    _webhook_history_and_org_for_run_detail,
    enrich_webhooks_run_org,
)
from jsonutil import dumps_pretty, loads_path
from models import ManifestEntry, RunManifest

router = APIRouter()

_IPD_TERMINAL = {"completed", "returned", "failed"}

_fire_locks: dict[str, asyncio.Lock] = {}


async def _fire_payment_order(
    client: AsyncModernTreasury,
    resolved: dict,
    *,
    idempotency_key: str,
) -> dict:
    result = await client.payment_orders.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    child_refs: dict[str, str] = {}
    if result.ledger_transaction_id:
        child_refs["ledger_transaction"] = result.ledger_transaction_id
    return {"created_id": result.id, "child_refs": child_refs}


async def _fire_expected_payment(
    client: AsyncModernTreasury,
    resolved: dict,
    *,
    idempotency_key: str,
) -> dict:
    result = await client.expected_payments.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    return {"created_id": result.id}


async def _fire_ledger_transaction(
    client: AsyncModernTreasury,
    resolved: dict,
    *,
    idempotency_key: str,
) -> dict:
    result = await client.ledger_transactions.create(
        **resolved,
        idempotency_key=idempotency_key,
    )
    return {"created_id": result.id}


async def _fire_incoming_payment_detail(
    client: AsyncModernTreasury,
    resolved: dict,
    *,
    idempotency_key: str,
) -> dict:
    result = await client.incoming_payment_details.create_async(
        **resolved,
        idempotency_key=idempotency_key,
    )

    @retry(
        wait=TENACITY_WAIT_EXP_2_10,
        stop=TENACITY_STOP_30,
        retry=retry_if_result(lambda r: r.status not in _IPD_TERMINAL),
    )
    async def _poll():
        return await client.incoming_payment_details.retrieve(result.id)

    try:
        ipd = await _poll()
    except RetryError as e:
        last = e.last_attempt.result()
        raise HTTPException(504, f"IPD did not complete within 30s (status: {last.status})")

    if ipd.status != "completed":
        raise HTTPException(
            502,
            f"IPD reached terminal state '{ipd.status}', not 'completed'",
        )

    child_refs: dict[str, str] = {}
    if ipd.transaction_id:
        child_refs["transaction"] = ipd.transaction_id
    if ipd.ledger_transaction_id:
        child_refs["ledger_transaction"] = ipd.ledger_transaction_id

    return {"created_id": result.id, "child_refs": child_refs}


_FIRE_DISPATCH = {
    "payment_order": _fire_payment_order,
    "expected_payment": _fire_expected_payment,
    "ledger_transaction": _fire_ledger_transaction,
    "incoming_payment_detail": _fire_incoming_payment_detail,
}

if frozenset(_FIRE_DISPATCH) != FIREABLE_TYPES:
    raise RuntimeError("_FIRE_DISPATCH keys must match dataloader.staged_fire.FIREABLE_TYPES")


@router.get("/runs/{run_id}", include_in_schema=False)
async def run_detail_page(
    request: Request,
    run_id: str,
    settings: SettingsDep,
    templates: TemplatesDep,
    current_user: CurrentAppUserDep,
):
    """Four-tab run detail page: Config, Resources, Staged, Webhooks."""
    runs_dir = Path(settings.runs_dir)

    manifest = await load_run_manifest_for_reader(request, settings, run_id, current_user)
    if manifest is None:
        raise HTTPException(404, f"Run '{run_id}' not found")
    correlation_state.ensure_run_indexed(run_id, manifest)

    config_path = runs_dir / f"{run_id}_config.json"
    config_json = config_path.read_text("utf-8") if config_path.exists() else "{}"

    staged_path = runs_dir / f"{run_id}_staged.json"
    staged_payloads: dict[str, dict] = {}
    if staged_path.exists():
        try:
            data = loads_path(staged_path)
            if isinstance(data, dict):
                staged_payloads = data
        except (json.JSONDecodeError, OSError, TypeError):
            pass

    webhook_history, rom = await _webhook_history_and_org_for_run_detail(
        request, run_id, current_user
    )
    enrich_webhooks_run_org(webhook_history, rom)

    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {
            "run_id": run_id,
            "manifest": manifest,
            "config_json": config_json,
            "staged_payloads": staged_payloads,
            "webhook_history": webhook_history,
        },
    )


@router.get("/api/runs/{run_id}/staged/drawer", include_in_schema=False)
async def staged_drawer(
    request: Request,
    run_id: str,
    settings: SettingsDep,
    templates: TemplatesDep,
    current_user: CurrentAppUserDep,
    ref: str = Query(...),
):
    """Return drawer HTML for a staged resource payload."""
    if not await run_is_readable(request, settings, run_id, current_user):
        raise HTTPException(404, "Run not found")

    runs_dir = Path(settings.runs_dir)

    staged_path = runs_dir / f"{run_id}_staged.json"
    if not staged_path.exists():
        raise HTTPException(404, "No staged payloads for this run")

    try:
        raw = loads_path(staged_path)
    except (json.JSONDecodeError, OSError, TypeError) as exc:
        raise HTTPException(404, f"Invalid staged file: {exc}") from exc
    if not isinstance(raw, dict):
        raise HTTPException(404, "Invalid staged file shape")
    staged_payloads: dict[str, dict] = raw
    if ref not in staged_payloads:
        raise HTTPException(404, f"Staged payload not found: {ref}")

    manifest_path = runs_dir / f"{run_id}.json"
    manifest = RunManifest.load(manifest_path) if manifest_path.exists() else None
    staged_at = ""
    if manifest and manifest.resources_staged:
        for s in manifest.resources_staged:
            if s.typed_ref == ref:
                staged_at = s.staged_at
                break

    return templates.TemplateResponse(
        request,
        "partials/staged_drawer.html",
        {"typed_ref": ref, "payload": staged_payloads[ref], "staged_at": staged_at},
    )


@router.post("/api/runs/{run_id}/fire/{typed_ref:path}")
async def fire_staged(
    request: Request,
    run_id: str,
    typed_ref: str,
    settings: SettingsDep,
    templates: TemplatesDep,
    current_user: CurrentAppUserDep,
    api_key: str = Form(...),
    org_id: str = Form(...),
):
    """Fire a staged resource — sends the resolved payload to the MT API."""
    if not await run_is_readable(request, settings, run_id, current_user):
        raise HTTPException(404, "Run not found")

    runs_dir = Path(settings.runs_dir)

    lock = _fire_locks.setdefault(run_id, asyncio.Lock())
    async with lock:
        staged_path = runs_dir / f"{run_id}_staged.json"
        if not staged_path.exists():
            raise HTTPException(404, "No staged payloads for this run")

        try:
            loaded = loads_path(staged_path)
        except (json.JSONDecodeError, OSError, TypeError) as exc:
            raise HTTPException(404, f"Invalid staged file: {exc}") from exc
        if not isinstance(loaded, dict):
            raise HTTPException(404, "Invalid staged file shape")
        staged_payloads = loaded
        if typed_ref not in staged_payloads:
            raise HTTPException(404, f"Staged payload not found: {typed_ref}")

        resolved = staged_payloads[typed_ref]
        resource_type = typed_ref.split(".")[0]

        handler = _FIRE_DISPATCH.get(resource_type)
        if not handler:
            supported = ", ".join(sorted(FIREABLE_TYPES))
            raise HTTPException(
                400,
                f"Cannot fire staged '{resource_type}'. Fireable types: {supported}",
            )

        async with AsyncModernTreasury(api_key=api_key, organization_id=org_id) as client:
            result = await handler(
                client,
                resolved,
                idempotency_key=f"{run_id}:staged:{typed_ref}",
            )

        manifest_path = runs_dir / f"{run_id}.json"
        manifest = RunManifest.load(manifest_path)
        manifest.resources_created.append(
            ManifestEntry(
                batch=-1,
                resource_type=resource_type,
                typed_ref=typed_ref,
                created_id=result["created_id"],
                created_at=_now_iso(),
                deletable=DELETABILITY.get(resource_type, False),
                child_refs=result.get("child_refs", {}),
            )
        )
        manifest.resources_staged = [
            s for s in manifest.resources_staged if s.typed_ref != typed_ref
        ]
        manifest.write(settings.runs_dir)

        del staged_payloads[typed_ref]
        if staged_payloads:
            staged_path.write_text(dumps_pretty(staged_payloads), encoding="utf-8")
        else:
            staged_path.unlink(missing_ok=True)

    correlation_state.index_resource(run_id, result["created_id"], typed_ref)
    for child_key, child_id in result.get("child_refs", {}).items():
        correlation_state.index_resource(run_id, child_id, f"{typed_ref}.{child_key}")

    html = templates.get_template("partials/staged_row_fired.html").render(
        s_typed_ref=typed_ref,
        resource_type=resource_type,
        created_id=result["created_id"],
        child_refs=result.get("child_refs", {}),
    )
    return HTMLResponse(html)
