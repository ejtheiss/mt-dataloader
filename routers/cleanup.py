"""Cleanup routes: initiate cleanup and SSE stream."""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request
from loguru import logger
from modern_treasury import AsyncModernTreasury
from sse_starlette import EventSourceResponse, ServerSentEvent

from engine import RefRegistry
from handlers import DELETABILITY
from helpers import error_html, error_response
from models import DataLoaderConfig, RunManifest
from routers.deps import SettingsDep, TemplatesDep
from session import SessionState, sessions
from sse_helpers import sse_error_response

router = APIRouter(tags=["cleanup"])


@router.post("/api/cleanup/{run_id}")
async def cleanup_page(
    request: Request,
    run_id: str,
    settings: SettingsDep,
    templates: TemplatesDep,
    api_key: str = Form(...),
    org_id: str = Form(...),
):
    """Return cleanup page with pre-rendered rows and SSE container."""
    manifest_path = Path(settings.runs_dir) / f"{run_id}.json"
    if not manifest_path.exists():
        return error_response("Not Found", f"Run '{run_id}' not found", 404)

    manifest = RunManifest.load(manifest_path)
    token = f"cleanup-{secrets.token_urlsafe(16)}"
    sessions[token] = SessionState(
        session_token=token,
        api_key=api_key,
        org_id=org_id,
        config=DataLoaderConfig(),
        registry=RefRegistry(),
        batches=[],
        config_json_text="{}",
        cleanup_manifest=manifest,
    )

    reversed_resources = list(reversed(manifest.resources_created))
    return templates.TemplateResponse(
        request,
        "cleanup.html",
        {
            "cleanup_token": token,
            "run_id": run_id,
            "resources": reversed_resources,
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

    manifest = session.cleanup_manifest
    if manifest is None:
        return sse_error_response(
            error_html=error_html,
            title="Session Error",
            detail="Cleanup manifest missing from session.",
        )

    async def cleanup_generator():
        async with AsyncModernTreasury(
            api_key=session.api_key, organization_id=session.org_id
        ) as client:
            reversed_resources = list(reversed(manifest.resources_created))

            try:
                for entry in reversed_resources:
                    action, status = await _cleanup_one(client, entry)
                    html = templates.get_template(
                        "partials/cleanup_row.html"
                    ).render(entry=entry, action=action, status=status)
                    yield ServerSentEvent(
                        data=html, event="cleanup_progress"
                    )

                html = templates.get_template(
                    "partials/cleanup_complete.html"
                ).render(run_id=manifest.run_id)
                yield ServerSentEvent(
                    data=html, event="cleanup_complete"
                )
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
            await client.ledger_transactions.update(
                entry.created_id, status="archived"
            )
            return ("archived", "success")

        elif rtype == "category_membership":
            cat_id, la_id = entry.created_id.split(":", 1)
            await client.ledger_account_categories.remove_ledger_account(
                la_id, id=cat_id
            )
            return ("removed", "success")

        elif rtype == "nested_category":
            parent_id, sub_id = entry.created_id.split(":", 1)
            await client.ledger_account_categories.remove_nested_category(
                sub_id, id=parent_id
            )
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


def _get_resource_client(
    client: AsyncModernTreasury, resource_type: str
) -> Any | None:
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
