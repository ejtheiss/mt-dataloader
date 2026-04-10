"""Setup shell pages: redirect, setup, preview."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from dataloader.engine import config_hash
from dataloader.helpers import (
    build_available_connections,
    build_discovered_by_type,
    build_flow_grouped_preview,
)
from dataloader.routers.deps import AsyncSessionDep, OptionalSessionQueryDep, TemplatesDep
from dataloader.session.draft_persist import run_access_context_for_request
from db.repositories import loader_drafts as drafts_repo
from models import DisplayPhase


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

        session_token = sess.session_token
        total_resources = sum(len(b) for b in sess.batches)
        deletable_count = sum(1 for i in sess.preview_items if i["deletable"])
        non_deletable_count = sum(1 for i in sess.preview_items if not i["deletable"])

        if sess.flow_ir:
            flow_groups = build_flow_grouped_preview(sess)
            return templates.TemplateResponse(
                request,
                "preview_flows_page.html",
                {
                    "session_token": session_token,
                    "flow_groups": flow_groups,
                    "resource_count": total_resources,
                    "deletable_count": deletable_count,
                    "non_deletable_count": non_deletable_count,
                    "discovery": sess.discovery,
                    "reconciliation": sess.reconciliation,
                    "config_json_text": sess.config_json_text,
                    "has_funds_flows": True,
                    "mermaid_diagrams": sess.mermaid_diagrams or [],
                    "available_connections": build_available_connections(
                        sess.config,
                        sess.discovery,
                    ),
                },
            )

        return templates.TemplateResponse(
            request,
            "preview_page.html",
            {
                "session_token": session_token,
                "batches": sess.batches,
                "preview_items": sess.preview_items,
                "config_hash": config_hash(sess.config),
                "resource_count": total_resources,
                "deletable_count": deletable_count,
                "non_deletable_count": non_deletable_count,
                "display_phases": DisplayPhase,
                "discovery": sess.discovery,
                "reconciliation": sess.reconciliation,
                "config_json_text": sess.config_json_text,
                "discovered_by_type": build_discovered_by_type(sess.discovery),
                "has_funds_flows": False,
                "available_connections": build_available_connections(
                    sess.config,
                    sess.discovery,
                ),
            },
        )
