"""Process-lifetime webhook buffer, deduplication, SSE listener registry, signature client.

Single ownership for module-level mutables shared by ingest, stream, and drawer routes.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass

from modern_treasury import AsyncModernTreasury


@dataclass
class WebhookEntry:
    """Single received webhook, stored in memory and in ``webhook_events``."""

    received_at: str
    event_type: str
    resource_type: str
    resource_id: str
    webhook_id: str
    run_id: str | None
    typed_ref: str | None
    raw: dict
    html: str = ""
    mt_org_id: str | None = None


_webhook_buffer: deque[WebhookEntry] = deque(maxlen=500)

_seen_ids_order: deque[str] = deque(maxlen=2000)
_seen_ids: set[str] = set()

_webhook_listeners: list[tuple[str | None, asyncio.Queue[WebhookEntry]]] = []

_sig_client: AsyncModernTreasury | None = None


def _mark_seen(webhook_id: str) -> bool:
    """Track a webhook ID for dedup.  Returns True if already seen."""
    if webhook_id in _seen_ids:
        return True
    if len(_seen_ids_order) == _seen_ids_order.maxlen:
        evicted = _seen_ids_order[0]
        _seen_ids.discard(evicted)
    _seen_ids_order.append(webhook_id)
    _seen_ids.add(webhook_id)
    return False


def _get_sig_client(secret: str) -> AsyncModernTreasury:
    """Lazy singleton for webhook signature verification."""
    global _sig_client
    if _sig_client is None:
        _sig_client = AsyncModernTreasury(
            api_key="unused",
            organization_id="unused",
            webhook_key=secret,
        )
    return _sig_client


async def _fanout(entry: WebhookEntry) -> None:
    """Push a webhook entry to all connected SSE listeners."""
    dead: list[int] = []
    for i, (filter_run_id, q) in enumerate(_webhook_listeners):
        if filter_run_id is None or filter_run_id == entry.run_id:
            try:
                q.put_nowait(entry)
            except asyncio.QueueFull:
                dead.append(i)
    for i in reversed(dead):
        _webhook_listeners.pop(i)
