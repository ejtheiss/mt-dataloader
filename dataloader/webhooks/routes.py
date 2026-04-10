"""Compose webhook sub-routers (Plan 07 — physical split of the former monolith).

Package entrypoint: ``dataloader.webhooks`` re-exports ``router`` from
``dataloader.webhooks.__init__`` (FastAPI *bigger applications* pattern:
https://fastapi.tiangolo.com/tutorial/bigger-applications/ ).

Submodules (no ``prefix=`` on ``include_router`` — routes use absolute paths):

- ``ingest`` — POST ``/webhooks/mt``
- ``stream_fanout`` — GET ``/webhooks/stream``
- ``runs_staged`` — run detail, staged drawer, fire staged
- ``listen_tunnel`` — GET ``/listen``
- ``webhook_drawers_test`` — drawer + test inject

Shared state: ``buffer_state`` (ring buffer, dedup, SSE listeners, sig client).
Persistence / history helpers: ``webhook_persist``.
"""

from __future__ import annotations

from fastapi import APIRouter

from dataloader.webhooks.ingest import router as ingest_router
from dataloader.webhooks.listen_tunnel import router as listen_tunnel_router
from dataloader.webhooks.runs_staged import router as runs_staged_router
from dataloader.webhooks.stream_fanout import router as stream_fanout_router
from dataloader.webhooks.webhook_drawers_test import router as webhook_drawers_test_router

router = APIRouter()
router.include_router(ingest_router)
router.include_router(stream_fanout_router)
router.include_router(runs_staged_router)
router.include_router(listen_tunnel_router)
router.include_router(webhook_drawers_test_router)
