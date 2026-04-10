"""Loader draft restore / discard."""

from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from dataloader.helpers import error_response
from dataloader.loader_validation import (
    LoaderValidationFailure,
    apply_loader_validation_success_to_session,
    run_loader_validation_pipeline,
)
from dataloader.routers.deps import AsyncSessionDep, TemplatesDep
from dataloader.routers.setup._helpers import (
    pipeline_error_response,
    render_preview_or_redirect,
)
from dataloader.session import prune_expired_sessions, sessions
from dataloader.session.draft_persist import (
    merge_loader_draft_into_session,
    persist_loader_draft,
    run_access_context_for_request,
)
from db.repositories import loader_drafts as drafts_repo


def register_drafts(router: APIRouter) -> None:
    @router.post("/api/draft/restore", include_in_schema=False)
    async def restore_draft(
        request: Request,
        templates: TemplatesDep,
        api_key: str = Form(...),
        org_id: str = Form(...),
        org_name: str = Form(""),
    ):
        """Reload stored config through the validate pipeline (API key from client only)."""
        prune_expired_sessions()

        factory = getattr(request.app.state, "async_session_factory", None)
        if factory is None:
            return error_response("Database unavailable", "Cannot restore draft.")

        ctx = run_access_context_for_request(request)
        async with factory() as db:
            draft = await drafts_repo.get_loader_draft(db, ctx.user_id, ctx)
        if draft is None:
            return error_response("No saved draft", "Validate a config first to create one.")

        raw_json = draft.config_json_text.encode()
        outcome = await run_loader_validation_pipeline(raw_json, api_key, org_id)
        if isinstance(outcome, LoaderValidationFailure):
            return pipeline_error_response(outcome)

        ol = org_name.strip() or draft.org_label or None
        session = apply_loader_validation_success_to_session(
            outcome,
            api_key,
            org_id,
            org_label=ol,
            working_config_json=draft.working_config_json,
            generation_recipes=draft.generation_recipes,
        )
        merge_loader_draft_into_session(session, draft)
        sessions[session.session_token] = session
        await persist_loader_draft(request, session)
        return render_preview_or_redirect(request, session, templates)

    @router.post("/api/draft/discard", include_in_schema=False)
    async def discard_loader_draft(request: Request, db_session: AsyncSessionDep):
        """Explicitly remove the durable draft for the current app user (runs unchanged)."""
        ctx = run_access_context_for_request(request)
        await drafts_repo.delete_loader_draft(db_session, ctx.user_id, ctx)
        await db_session.commit()
        return RedirectResponse(url="/setup", status_code=303)
