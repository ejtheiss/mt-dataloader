"""Fund Flows routes — aggregated router (Plan 10a package split)."""

from __future__ import annotations

from fastapi import APIRouter

from dataloader.routers.flows.api import router as api_router
from dataloader.routers.flows.page import router as page_router
from dataloader.routers.flows.partials import router as partials_router

router = APIRouter()
router.include_router(page_router, tags=["flows"])
router.include_router(api_router, tags=["flows"])
router.include_router(partials_router, tags=["flows"])
