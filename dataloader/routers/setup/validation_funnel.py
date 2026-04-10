"""Shared ``run_loader_validation_pipeline`` → session swap → draft persist (HTMX + JSON v1).

Single funnel for revalidate flows so HTMX and § v1 JSON routes stay aligned (Plan 04).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from dataloader.loader_validation import (
    LoaderValidationFailure,
    LoaderValidationSuccess,
    apply_loader_validation_success_to_session,
    run_loader_validation_pipeline,
)
from dataloader.session import SessionState, sessions
from dataloader.session.draft_persist import persist_loader_draft


@dataclass(frozen=True)
class RevalidatedSession:
    """Successful revalidate: new session token + pipeline result for § v1 envelopes."""

    session: SessionState
    pipeline_success: LoaderValidationSuccess


async def revalidate_existing_session(
    request: Request,
    old_session: SessionState,
    *,
    raw_json: bytes,
    reconcile_overrides: dict | None = None,
    manual_mappings: dict | None = None,
    preserve_working_config: bool = True,
) -> LoaderValidationFailure | RevalidatedSession:
    """Run full loader pipeline for an existing session and replace it in ``sessions``.

    On success: stores the new session under a fresh token, removes the previous token,
    persists the loader draft, and returns :class:`RevalidatedSession` (for JSON v1
    ``loader_validation_success_to_v1_envelope``). On failure returns
    ``LoaderValidationFailure`` (no session mutation).

    ``preserve_working_config`` — when True, pass ``old_session.working_config_json`` into
    :func:`apply_loader_validation_success_to_session`; when False, pass ``None`` so the
    session working copy follows the compiled ``config_json_text`` (patch-json behavior).
    """
    outcome = await run_loader_validation_pipeline(
        raw_json,
        old_session.api_key,
        old_session.org_id,
        reconcile_overrides=reconcile_overrides,
        manual_mappings=manual_mappings,
        prior_config=old_session.config,
    )
    if isinstance(outcome, LoaderValidationFailure):
        return outcome

    working = old_session.working_config_json if preserve_working_config else None
    new_session = apply_loader_validation_success_to_session(
        outcome,
        old_session.api_key,
        old_session.org_id,
        org_label=getattr(old_session, "org_label", None),
        generation_recipes=old_session.generation_recipes,
        working_config_json=working,
    )
    prev_token = old_session.session_token
    sessions[new_session.session_token] = new_session
    del sessions[prev_token]
    await persist_loader_draft(request, new_session)
    return RevalidatedSession(session=new_session, pipeline_success=outcome)
