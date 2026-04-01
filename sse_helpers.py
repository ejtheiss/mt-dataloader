"""Shared Server-Sent Event helpers for HTML partial streams."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sse_starlette import EventSourceResponse, ServerSentEvent


def sse_error_response(
    *,
    error_html: Callable[[str, str], str],
    title: str,
    detail: str,
    ping: int | None = 15,
) -> EventSourceResponse:
    """Return an ``EventSourceResponse`` that emits one error partial then closes."""

    async def _gen():
        html = error_html(title, detail)
        yield ServerSentEvent(data=html, event="error")
        yield ServerSentEvent(data="", event="close")

    return EventSourceResponse(_gen(), ping=ping)


def make_emit_sse(
    templates: Any,
    queue: Any,
) -> Any:
    """Build an ``EmitFn`` that renders ``resource_row`` and enqueues ``ServerSentEvent``s."""

    async def emit(event_type: str, typed_ref: str, data: dict[str, Any]) -> None:
        context = {"ref": typed_ref, "status": event_type, **data}
        html = templates.get_template("partials/resource_row.html").render(context)
        await queue.put(ServerSentEvent(data=html, event=event_type))

    return emit
