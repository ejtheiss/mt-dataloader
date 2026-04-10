"""Setup routes: pages, JSON API v1, HTMX validate, drafts, resource partials."""

from __future__ import annotations

from fastapi import APIRouter

from dataloader.routers.setup.drafts import register_drafts
from dataloader.routers.setup.htmx_validate import register_htmx_validate
from dataloader.routers.setup.json_api import register_json_api
from dataloader.routers.setup.pages import register_pages
from dataloader.routers.setup.resource_partials import register_resource_partials

router = APIRouter(tags=["setup"])

register_pages(router)
register_json_api(router)
register_htmx_validate(router)
register_drafts(router)
register_resource_partials(router)

__all__ = ["router"]
