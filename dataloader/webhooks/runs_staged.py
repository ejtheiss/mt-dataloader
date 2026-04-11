"""Run detail page, staged drawer, and POST fire for staged resources."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from modern_treasury import AsyncModernTreasury
from tenacity import RetryError, retry, retry_if_result

from dataloader.handlers import DELETABILITY, TENACITY_STOP_30, TENACITY_WAIT_EXP_2_10
from dataloader.routers.deps import CurrentAppUserDep, SettingsDep, TemplatesDep
from dataloader.run_access import get_run_detail_view, run_is_readable
from dataloader.staged_fire import FIREABLE_TYPES
from dataloader.webhooks import correlation_state
from dataloader.webhooks.webhook_persist import (
    _webhook_history_and_org_for_run_detail,
    enrich_webhooks_run_org,
)
from db.repositories import run_artifacts, runs as runs_repo
from dataloader.engine.run_meta import _now_iso
from dataloader.run_access import user_to_ctx
from models import ManifestEntry

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
    run_detail = await get_run_detail_view(request, settings, run_id, current_user)
    if run_detail is None:
        raise HTTPException(404, f"Run '{run_id}' not found")
    correlation_state.ensure_run_indexed_from_rows(run_id, run_detail.resources_created)

    webhook_history, rom = await _webhook_history_and_org_for_run_detail(
        request, run_id, current_user
    )
    enrich_webhooks_run_org(webhook_history, rom)

    return templates.TemplateResponse(
        request,
        "run_detail.html",
        {
            "run_id": run_id,
            "run_detail": run_detail,
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

    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is None:
        raise HTTPException(503, "Database unavailable")

    ctx = user_to_ctx(current_user)
    async with factory() as session:
        loaded = await run_artifacts.fetch_staged_payload_and_meta(session, run_id, ref, ctx)
    if loaded is None:
        raise HTTPException(404, "Staged payload not found")
    payload, staged_at = loaded

    return templates.TemplateResponse(
        request,
        "partials/staged_drawer.html",
        {"typed_ref": ref, "payload": payload, "staged_at": staged_at},
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

    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is None:
        raise HTTPException(503, "Database unavailable")

    lock = _fire_locks.setdefault(run_id, asyncio.Lock())
    async with lock:
        ctx = user_to_ctx(current_user)
        async with factory() as session:
            loaded = await run_artifacts.fetch_staged_payload_and_meta(
                session, run_id, typed_ref, ctx
            )
            if loaded is None:
                raise HTTPException(404, "Staged payload not found")
            resolved, _st = loaded

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

        entry = ManifestEntry(
            batch=-1,
            resource_type=resource_type,
            typed_ref=typed_ref,
            created_id=result["created_id"],
            created_at=_now_iso(),
            deletable=DELETABILITY.get(resource_type, False),
            child_refs=result.get("child_refs", {}),
        )

        async with factory() as session:
            async with session.begin():
                await run_artifacts.delete_staged_item(session, run_id, typed_ref)
                await run_artifacts.insert_created_resource_row(
                    session,
                    run_id=run_id,
                    batch=entry.batch,
                    resource_type=entry.resource_type,
                    typed_ref=entry.typed_ref,
                    created_id=entry.created_id,
                    created_at=entry.created_at,
                    deletable=entry.deletable,
                    child_refs=dict(entry.child_refs),
                    cleanup_status=entry.cleanup_status,
                )
                await runs_repo.sync_artifact_counts_from_tables(session, run_id)

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
