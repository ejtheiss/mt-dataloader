"""Execute routes: execute page and SSE stream."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Request
from loguru import logger
from modern_treasury import AsyncModernTreasury
from sse_starlette import EventSourceResponse, ServerSentEvent

from dataloader.engine import execute, generate_run_id
from dataloader.handlers import build_handler_dispatch, build_update_dispatch
from dataloader.routers.deps import SessionFormDep, SettingsDep, TemplatesDep
from dataloader.webhooks import index_resource
from helpers import error_html, error_response
from models import DisplayPhase
from session import sessions
from sse_helpers import make_emit_sse, sse_error_response

router = APIRouter(tags=["execute"])


@router.post("/api/execute")
async def execute_page(
    request: Request,
    templates: TemplatesDep,
    sess: SessionFormDep,
):
    """Return execute page with pre-rendered rows and SSE container."""
    if not sess:
        return error_response("Session Expired", "Please re-validate your config.")

    return templates.TemplateResponse(
        request,
        "execute.html",
        {
            "session_token": sess.session_token,
            "preview_items": sess.preview_items,
            "batches": sess.batches,
            "resource_count": sum(len(b) for b in sess.batches),
            "display_phases": DisplayPhase,
        },
    )


@router.get("/api/execute/stream")
async def execute_stream(
    templates: TemplatesDep,
    settings: SettingsDep,
    session_token: str,
):
    """SSE stream endpoint. Pops session and runs the DAG engine."""
    session = sessions.pop(session_token, None)
    if not session:
        return sse_error_response(
            error_html=error_html,
            title="Session Expired",
            detail="Please re-validate your config.",
        )

    async def event_generator():
        queue: asyncio.Queue[ServerSentEvent | None] = asyncio.Queue()
        emit_sse = make_emit_sse(templates, queue)
        run_id = generate_run_id()
        semaphore = asyncio.Semaphore(settings.max_concurrent_requests)

        config_path = Path(settings.runs_dir) / f"{run_id}_config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(session.config_json_text, encoding="utf-8")

        disconnected = False

        async def run_engine():
            nonlocal disconnected
            async with AsyncModernTreasury(
                api_key=session.api_key,
                organization_id=session.org_id,
            ) as client:
                handler_dispatch = build_handler_dispatch(client, emit_sse)
                update_dispatch = build_update_dispatch(client, emit_sse)
                try:
                    manifest = await execute(
                        config=session.config,
                        registry=session.registry,
                        handler_dispatch=handler_dispatch,
                        run_id=run_id,
                        semaphore=semaphore,
                        emit_sse=emit_sse,
                        is_disconnected=lambda: disconnected,
                        runs_dir=settings.runs_dir,
                        on_resource_created=index_resource,
                        skip_refs=session.skip_refs or None,
                        update_refs=session.update_refs or None,
                        update_dispatch=update_dispatch,
                        mt_org_id=session.org_id,
                        mt_org_label=session.org_label,
                    )
                    html = templates.get_template("partials/run_complete.html").render(
                        manifest=manifest, run_id=run_id
                    )
                    await queue.put(ServerSentEvent(data=html, event="run_complete"))
                except Exception as exc:
                    logger.bind(run_id=run_id, error=str(exc)).error("Execution failed")
                    html = templates.get_template("partials/error.html").render(error=str(exc))
                    await queue.put(ServerSentEvent(data=html, event="error"))
                finally:
                    await queue.put(ServerSentEvent(data="", event="close"))
                    await queue.put(None)

        task = asyncio.create_task(run_engine())

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        except asyncio.CancelledError:
            disconnected = True
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    return EventSourceResponse(event_generator(), ping=15)
