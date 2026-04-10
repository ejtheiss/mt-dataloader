"""HTMX validate / revalidate (multipart form → preview or error)."""

from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, Request, UploadFile

from dataloader.helpers import error_response
from dataloader.loader_validation import (
    LoaderValidationFailure,
    apply_loader_validation_success_to_session,
    run_loader_validation_pipeline,
)
from dataloader.routers.deps import SessionFormDep, TemplatesDep
from dataloader.routers.setup._helpers import (
    pipeline_error_response,
    render_preview_or_redirect,
)
from dataloader.session import prune_expired_sessions, sessions
from dataloader.session.draft_persist import persist_loader_draft
from jsonutil import loads_str


def register_htmx_validate(router: APIRouter) -> None:
    @router.post("/api/validate")
    async def validate(
        request: Request,
        templates: TemplatesDep,
        api_key: str = Form(...),
        org_id: str = Form(...),
        org_name: str = Form(""),
        config_file: UploadFile | None = File(None),
        config_json: str | None = Form(None),
    ):
        """Validate API key, discover org, parse config, compile, compute DAG, cache state."""
        prune_expired_sessions()

        if config_json and config_json.strip():
            raw_json = config_json.strip().encode()
        elif config_file and config_file.size:
            raw_json = await config_file.read()
        else:
            return error_response("Missing Config", "Upload a JSON file or paste JSON directly.")

        outcome = await run_loader_validation_pipeline(raw_json, api_key, org_id)
        if isinstance(outcome, LoaderValidationFailure):
            return pipeline_error_response(outcome)

        ol = org_name.strip() or None
        session = apply_loader_validation_success_to_session(outcome, api_key, org_id, org_label=ol)
        sessions[session.session_token] = session
        await persist_loader_draft(request, session)
        return render_preview_or_redirect(request, session, templates)

    @router.post("/api/revalidate")
    async def revalidate(
        request: Request,
        templates: TemplatesDep,
        old_session: SessionFormDep,
        config_json: str = Form(...),
        reconcile_overrides: str | None = Form(None),
    ):
        """Re-validate edited JSON using credentials from an existing session."""
        if not old_session:
            return error_response("Session Expired", "Please start over from Setup.")

        raw_json = config_json.strip().encode()
        prev_token = old_session.session_token

        overrides: dict = {}
        manual_maps: dict = {}
        if reconcile_overrides:
            try:
                raw_ov = loads_str(reconcile_overrides)
                overrides = raw_ov.get("overrides", raw_ov) if isinstance(raw_ov, dict) else {}
                if isinstance(raw_ov, dict):
                    manual_maps = raw_ov.get("manual_mappings", {})
            except json.JSONDecodeError:
                pass

        outcome = await run_loader_validation_pipeline(
            raw_json,
            old_session.api_key,
            old_session.org_id,
            reconcile_overrides=overrides,
            manual_mappings=manual_maps,
            prior_config=old_session.config,
        )
        if isinstance(outcome, LoaderValidationFailure):
            return pipeline_error_response(outcome)

        session = apply_loader_validation_success_to_session(
            outcome,
            old_session.api_key,
            old_session.org_id,
            org_label=getattr(old_session, "org_label", None),
            generation_recipes=old_session.generation_recipes,
            working_config_json=old_session.working_config_json,
        )
        sessions[session.session_token] = session
        del sessions[prev_token]

        await persist_loader_draft(request, session)
        return render_preview_or_redirect(request, session, templates)
