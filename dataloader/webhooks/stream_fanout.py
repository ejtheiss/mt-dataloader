"""GET ``/webhooks/stream`` — SSE fan-out of incoming webhooks."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from sse_starlette import EventSourceResponse, ServerSentEvent

from dataloader.routers.deps import CurrentAppUserDep, SettingsDep
from dataloader.run_access import run_is_readable
from dataloader.webhooks.buffer_state import WebhookEntry, _webhook_buffer, _webhook_listeners
from models import AppSettings, CurrentAppUser

router = APIRouter()


async def _sse_entry_visible(
    request: Request,
    settings: AppSettings,
    entry: WebhookEntry,
    user: CurrentAppUser,
    *,
    filter_run_id: str | None,
) -> bool:
    if filter_run_id is not None:
        return entry.run_id == filter_run_id
    if user.is_admin:
        return True
    if entry.run_id is None:
        return False
    return await run_is_readable(request, settings, entry.run_id, user)


@router.get("/webhooks/stream")
async def webhook_stream(
    request: Request,
    settings: SettingsDep,
    current_user: CurrentAppUserDep,
    run_id: str | None = None,
    no_replay: bool = False,
):
    """SSE stream of incoming webhooks.

    With ``run_id``, only events for that run (must be readable). Without ``run_id``,
    **admin** receives every event; **user** receives events for runs they can read
    (unmatched / NULL ``run_id`` events are skipped).

    Pass ``no_replay=true`` to skip ring-buffer replay (used by the run
    detail page where historical webhooks are already server-rendered).
    """
    rid = (run_id or "").strip()
    filter_run_id = rid if rid else None
    if filter_run_id is not None and not await run_is_readable(
        request, settings, filter_run_id, current_user
    ):
        raise HTTPException(status_code=404, detail="Run not found")

    async def event_generator():
        q: asyncio.Queue[WebhookEntry] = asyncio.Queue(maxsize=100)
        listener = (filter_run_id, q)
        _webhook_listeners.append(listener)

        try:
            if not no_replay:
                for entry in list(_webhook_buffer):
                    if await _sse_entry_visible(
                        request, settings, entry, current_user, filter_run_id=filter_run_id
                    ):
                        yield ServerSentEvent(data=entry.html, event="webhook")

            while True:
                entry = await q.get()
                if await _sse_entry_visible(
                    request, settings, entry, current_user, filter_run_id=filter_run_id
                ):
                    yield ServerSentEvent(data=entry.html, event="webhook")

        except asyncio.CancelledError:
            pass
        finally:
            try:
                _webhook_listeners.remove(listener)
            except ValueError:
                pass

    return EventSourceResponse(event_generator(), ping=15)
