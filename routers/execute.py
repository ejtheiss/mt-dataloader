"""Execute routes: execute page and SSE stream."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request
from loguru import logger
from modern_treasury import AsyncModernTreasury
from sse_starlette import EventSourceResponse, ServerSentEvent

from engine import execute, generate_run_id
from handlers import build_handler_dispatch
from helpers import error_html, error_response, get_templates
from models import DisplayPhase
from session import sessions
from webhooks import index_resource

router = APIRouter(tags=["execute"])


def _make_emit_sse(
    queue: asyncio.Queue[ServerSentEvent | None],
) -> Any:
    """Return an EmitFn that renders context and enqueues ServerSentEvents."""
    templates = get_templates()

    async def emit(event_type: str, typed_ref: str, data: dict[str, Any]) -> None:
        context = {"ref": typed_ref, "status": event_type, **data}
        html = templates.get_template("partials/resource_row.html").render(context)
        await queue.put(ServerSentEvent(data=html, event=event_type))

    return emit


@router.post("/api/execute")
async def execute_page(
    request: Request,
    session_token: str = Form(...),
):
    """Return execute page with pre-rendered rows and SSE container."""
    templates = get_templates()
    session = sessions.get(session_token)
    if not session:
        return error_response("Session Expired", "Please re-validate your config.")

    return templates.TemplateResponse(
        request,
        "execute.html",
        {
            "session_token": session_token,
            "preview_items": session.preview_items,
            "batches": session.batches,
            "resource_count": sum(len(b) for b in session.batches),
            "display_phases": DisplayPhase,
        },
    )


@router.get("/api/execute/stream")
async def execute_stream(
    request: Request,
    session_token: str,
):
    """SSE stream endpoint. Pops session and runs the DAG engine."""
    templates = get_templates()
    session = sessions.pop(session_token, None)
    if not session:
        async def _error_gen():
            html = error_html("Session Expired", "Please re-validate your config.")
            yield ServerSentEvent(data=html, event="error")
            yield ServerSentEvent(data="", event="close")
        return EventSourceResponse(_error_gen())

    async def event_generator():
        queue: asyncio.Queue[ServerSentEvent | None] = asyncio.Queue()
        emit_sse = _make_emit_sse(queue)
        run_id = generate_run_id()
        settings = request.app.state.settings
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
                    )
                    html = templates.get_template(
                        "partials/run_complete.html"
                    ).render(manifest=manifest, run_id=run_id)
                    await queue.put(
                        ServerSentEvent(data=html, event="run_complete")
                    )
                except Exception as exc:
                    logger.bind(run_id=run_id, error=str(exc)).error(
                        "Execution failed"
                    )
                    html = templates.get_template("partials/error.html").render(
                        error=str(exc)
                    )
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
