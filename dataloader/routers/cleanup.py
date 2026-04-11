"""Cleanup routes: initiate cleanup and SSE stream.

Uses a separate in-memory ``SessionState`` for cleanup SSE only — does not
delete or modify ``loader_drafts`` (Wave D continuity).
"""

from __future__ import annotations

import asyncio
import secrets
from typing import Any

from fastapi import APIRouter, Form, Request
from loguru import logger
from modern_treasury import AsyncModernTreasury
from sse_starlette import EventSourceResponse, ServerSentEvent

from dataloader.engine import RefRegistry
from dataloader.handlers import DELETABILITY
from dataloader.helpers import error_html, error_response
from dataloader.routers.deps import CurrentAppUserDep, SettingsDep, TemplatesDep
from dataloader.run_access import user_to_ctx
from dataloader.session import SessionState, sessions
from dataloader.sse_helpers import sse_error_response
from db.repositories import run_artifacts
from models import DataLoaderConfig

router = APIRouter(tags=["cleanup"])


@router.post("/api/cleanup/{run_id}")
async def cleanup_page(
    request: Request,
    run_id: str,
    settings: SettingsDep,
    templates: TemplatesDep,
    current_user: CurrentAppUserDep,
    api_key: str = Form(...),
    org_id: str = Form(...),
):
    """Return cleanup page with pre-rendered rows and SSE container."""
    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is None:
        return error_response("Service Unavailable", "Database required for cleanup.", 503)
    ctx = user_to_ctx(current_user)
    try:
        async with factory() as session:
            from db.repositories import runs as runs_repo

            row = await runs_repo.get_run_row_for_access(session, run_id, ctx)
            if row is None:
                return error_response("Not Found", f"Run '{run_id}' not found", 404)
            resources = await run_artifacts.fetch_cleanup_created_rows(session, run_id, ctx)
    except Exception as exc:
        logger.bind(run_id=run_id).warning("cleanup load failed: {}", exc)
        return error_response("Error", "Could not load run resources.", 503)

    token = f"cleanup-{secrets.token_urlsafe(16)}"
    sessions[token] = SessionState(
        session_token=token,
        api_key=api_key,
        org_id=org_id,
        config=DataLoaderConfig(),
        registry=RefRegistry(),
        batches=[],
        config_json_text="{}",
        cleanup_resources=tuple(resources),
        cleanup_run_id=run_id,
    )

    return templates.TemplateResponse(
        request,
        "cleanup.html",
        {
            "cleanup_token": token,
            "run_id": run_id,
            "resources": resources,
            "deletability": DELETABILITY,
        },
    )


@router.get("/api/cleanup/stream/{token}")
async def cleanup_stream(token: str, templates: TemplatesDep):
    """SSE stream for cleanup progress."""
    session = sessions.pop(token, None)
    if not session:
        return sse_error_response(
            error_html=error_html,
            title="Session Expired",
            detail="Cleanup session not found.",
        )

    resources = session.cleanup_resources
    if resources is None:
        return sse_error_response(
            error_html=error_html,
            title="Session Error",
            detail="Cleanup resources missing from session.",
        )

    rid = session.cleanup_run_id or ""

    async def cleanup_generator():
        async with AsyncModernTreasury(
            api_key=session.api_key, organization_id=session.org_id
        ) as client:
            try:
                for entry in resources:
                    action, status = await _cleanup_one(client, entry)
                    html = templates.get_template("partials/cleanup_row.html").render(
                        entry=entry, action=action, status=status
                    )
                    yield ServerSentEvent(data=html, event="cleanup_progress")

                html = templates.get_template("partials/cleanup_complete.html").render(run_id=rid)
                yield ServerSentEvent(data=html, event="cleanup_complete")
            except asyncio.CancelledError:
                pass
            finally:
                yield ServerSentEvent(data="", event="close")

    return EventSourceResponse(cleanup_generator(), ping=15)


# ---------------------------------------------------------------------------
# Cleanup helpers
# ---------------------------------------------------------------------------


async def _cleanup_one(
    client: AsyncModernTreasury,
    entry: Any,
) -> tuple[str, str]:
    """Dispatch a single cleanup action."""
    rtype = entry.resource_type

    try:
        if rtype == "ledger_transaction":
            await client.ledger_transactions.update(entry.created_id, status="archived")
            return ("archived", "success")

        elif rtype == "category_membership":
            cat_id, la_id = entry.created_id.split(":", 1)
            await client.ledger_account_categories.remove_ledger_account(la_id, id=cat_id)
            return ("removed", "success")

        elif rtype == "nested_category":
            parent_id, sub_id = entry.created_id.split(":", 1)
            await client.ledger_account_categories.remove_nested_category(sub_id, id=parent_id)
            return ("removed", "success")

        elif entry.deletable:
            resource_client = _get_resource_client(client, rtype)
            if resource_client is None:
                return ("skipped", f"no cleanup handler for {rtype}")
            await resource_client.delete(entry.created_id)
            return ("deleted", "success")

        else:
            return ("skipped", "not deletable")

    except Exception as e:
        logger.bind(ref=entry.typed_ref, error=str(e)).warning("Cleanup failed")
        return ("failed", str(e))


def _get_resource_client(client: AsyncModernTreasury, resource_type: str) -> Any | None:
    """Map a resource type string to its SDK sub-client for deletion."""
    return {
        "counterparty": client.counterparties,
        "external_account": client.external_accounts,
        "virtual_account": client.virtual_accounts,
        "ledger": client.ledgers,
        "ledger_account": client.ledger_accounts,
        "ledger_account_category": client.ledger_account_categories,
        "expected_payment": client.expected_payments,
    }.get(resource_type)
