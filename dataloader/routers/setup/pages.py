"""Setup shell pages: redirect, setup, preview."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from dataloader.routers.deps import AsyncSessionDep, OptionalSessionQueryDep, TemplatesDep
from dataloader.session.draft_persist import run_access_context_for_request
from dataloader.view_models.setup_preview import (
    flat_preview_template_context,
    flow_preview_template_context,
)
from db.repositories import loader_drafts as drafts_repo


def register_pages(router: APIRouter) -> None:
    @router.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/setup")

    @router.get("/setup", include_in_schema=False)
    async def setup_page(
        request: Request,
        templates: TemplatesDep,
        db_session: AsyncSessionDep,
    ):
        ctx = run_access_context_for_request(request)
        row = await drafts_repo.get_loader_draft_row(db_session, ctx.user_id, ctx)
        has_saved_draft = row is not None
        return templates.TemplateResponse(
            request,
            "setup.html",
            {"title": "Setup", "has_saved_draft": has_saved_draft},
        )

    @router.get("/preview", include_in_schema=False)
    async def preview_page(
        request: Request,
        templates: TemplatesDep,
        sess: OptionalSessionQueryDep,
    ):
        """Preview page — flow-grouped when funds_flows present, flat otherwise."""
        if not sess:
            return RedirectResponse(url="/setup")

        if sess.flow_ir:
            return templates.TemplateResponse(
                request,
                "preview_flows_page.html",
                flow_preview_template_context(sess),
            )

        return templates.TemplateResponse(
            request,
            "preview_page.html",
            flat_preview_template_context(sess),
        )
