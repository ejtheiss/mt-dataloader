"""Loader JSON API v1: config/save, validate-json, revalidate-json, patch-json."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from dataloader.json_pointer import apply_json_pointer_set
from dataloader.loader_validation import (
    LoaderValidationFailure,
    apply_loader_validation_success_to_session,
    headless_outcome_to_envelope,
    invalid_body_kind_message,
    loader_validation_failure_to_envelope,
    loader_validation_success_to_v1_envelope,
    parse_loader_config_bytes,
    run_headless_validate_after_parse,
    run_loader_validation_pipeline,
)
from dataloader.routers.setup._helpers import (
    loader_setup_json_response,
    reconcile_pairs_from_optional_dict,
    session_working_config_dict,
)
from dataloader.session import sessions
from dataloader.session.draft_persist import persist_loader_draft
from jsonutil import dumps_pretty, loads_str
from models import ApplyConfigPatchJsonRequestV1, RevalidateJsonRequestV1
from models.loader_setup_json import (
    LoaderSetupEnvelopeV1,
    LoaderSetupErrorItem,
    error_items_from_pydantic_validation,
)


def register_json_api(router: APIRouter) -> None:
    @router.post(
        "/api/config/save",
        tags=["agent"],
        response_model=LoaderSetupEnvelopeV1,
        response_model_exclude_none=True,
        responses={
            404: {"model": LoaderSetupEnvelopeV1},
            422: {"model": LoaderSetupEnvelopeV1},
        },
    )
    async def save_config(request: Request) -> LoaderSetupEnvelopeV1 | JSONResponse:
        """Write edited config JSON back to the session and optionally to disk (JSON API v1 envelope)."""
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return loader_setup_json_response(
                LoaderSetupEnvelopeV1(
                    ok=False,
                    errors=[
                        LoaderSetupErrorItem(
                            code="invalid_body",
                            message="Request body must be JSON with session_token and config_json.",
                            path=None,
                        )
                    ],
                ),
                status_code=422,
            )

        if not isinstance(body, dict):
            return loader_setup_json_response(
                LoaderSetupEnvelopeV1(
                    ok=False,
                    errors=[
                        LoaderSetupErrorItem(
                            code="invalid_body",
                            message="Request body must be a JSON object.",
                            path=None,
                        )
                    ],
                ),
                status_code=422,
            )

        session_token = body.get("session_token", "")
        config_json = body.get("config_json", "")

        session = sessions.get(session_token)
        if not session:
            return loader_setup_json_response(
                LoaderSetupEnvelopeV1(
                    ok=False,
                    errors=[
                        LoaderSetupErrorItem(
                            code="session_expired",
                            message="Session expired or unknown session_token.",
                            path=None,
                        )
                    ],
                ),
                status_code=404,
            )

        if not isinstance(config_json, str):
            return loader_setup_json_response(
                LoaderSetupEnvelopeV1(
                    ok=False,
                    phase="parse",
                    errors=[
                        LoaderSetupErrorItem(
                            code="invalid_body",
                            message="config_json must be a string of JSON.",
                            path=None,
                        )
                    ],
                ),
                status_code=422,
            )

        pr = parse_loader_config_bytes(config_json.encode("utf-8"))
        if pr.body_invalid is not None:
            msg = invalid_body_kind_message(pr.body_invalid)
            if pr.body_invalid == "json":
                msg = f"config_json is not valid JSON: {msg}"
            return loader_setup_json_response(
                LoaderSetupEnvelopeV1(
                    ok=False,
                    phase="parse",
                    errors=[LoaderSetupErrorItem(code="invalid_body", message=msg, path=None)],
                ),
                status_code=422,
            )
        if pr.error is not None:
            return loader_setup_json_response(
                LoaderSetupEnvelopeV1(
                    ok=False,
                    phase="parse",
                    errors=error_items_from_pydantic_validation(pr.error),
                ),
                status_code=422,
            )
        if pr.config is None:
            return loader_setup_json_response(
                LoaderSetupEnvelopeV1(
                    ok=False,
                    phase="parse",
                    errors=[
                        LoaderSetupErrorItem(
                            code="validation_error",
                            message="Invalid configuration.",
                            path=None,
                        )
                    ],
                ),
                status_code=422,
            )

        config = pr.config
        session.config = config
        session.config_json_text = dumps_pretty(loads_str(config_json))
        session.working_config_json = session.config_json_text

        await persist_loader_draft(request, session)

        return LoaderSetupEnvelopeV1(
            ok=True,
            phase="complete",
            data={"message": "Config saved to session"},
        )

    @router.post(
        "/api/validate-json",
        tags=["agent"],
        response_model=LoaderSetupEnvelopeV1,
        response_model_exclude_none=True,
        responses={422: {"model": LoaderSetupEnvelopeV1}},
    )
    async def validate_json(request: Request) -> LoaderSetupEnvelopeV1 | JSONResponse:
        """Programmatic JSON validation endpoint for LLM repair loops (JSON API v1 envelope)."""
        body = await request.body()
        parsed = parse_loader_config_bytes(body)
        if parsed.body_invalid is not None:
            return loader_setup_json_response(
                LoaderSetupEnvelopeV1(
                    ok=False,
                    errors=[
                        LoaderSetupErrorItem(
                            code="invalid_body",
                            message=invalid_body_kind_message(parsed.body_invalid),
                            path=None,
                        )
                    ],
                ),
                status_code=422,
            )
        if parsed.error is not None:
            return LoaderSetupEnvelopeV1(
                ok=False,
                phase="parse",
                errors=error_items_from_pydantic_validation(parsed.error),
                data={},
            )
        if parsed.config is None:
            return LoaderSetupEnvelopeV1(
                ok=False,
                phase="parse",
                errors=[
                    LoaderSetupErrorItem(
                        code="validation_error",
                        message="Invalid configuration.",
                        path=None,
                    )
                ],
                data={},
            )

        outcome = run_headless_validate_after_parse(parsed.config, body)
        return headless_outcome_to_envelope(outcome)

    @router.post(
        "/api/revalidate-json",
        tags=["agent"],
        response_model=LoaderSetupEnvelopeV1,
        response_model_exclude_none=True,
        responses={
            404: {"model": LoaderSetupEnvelopeV1},
            422: {"model": LoaderSetupEnvelopeV1},
        },
    )
    async def revalidate_json(request: Request) -> LoaderSetupEnvelopeV1 | JSONResponse:
        """Re-validate config JSON using credentials from an existing session (§ v1 envelope)."""
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return loader_setup_json_response(
                LoaderSetupEnvelopeV1(
                    ok=False,
                    errors=[
                        LoaderSetupErrorItem(
                            code="invalid_body",
                            message="Request body must be JSON.",
                            path=None,
                        )
                    ],
                ),
                status_code=422,
            )
        if not isinstance(payload, dict):
            return loader_setup_json_response(
                LoaderSetupEnvelopeV1(
                    ok=False,
                    errors=[
                        LoaderSetupErrorItem(
                            code="invalid_body",
                            message="Request body must be a JSON object.",
                            path=None,
                        )
                    ],
                ),
                status_code=422,
            )
        try:
            body = RevalidateJsonRequestV1.model_validate(payload)
        except ValidationError as exc:
            return loader_setup_json_response(
                LoaderSetupEnvelopeV1(
                    ok=False,
                    errors=error_items_from_pydantic_validation(exc),
                ),
                status_code=422,
            )

        old_session = sessions.get(body.session_token)
        if not old_session:
            return loader_setup_json_response(
                LoaderSetupEnvelopeV1(
                    ok=False,
                    errors=[
                        LoaderSetupErrorItem(
                            code="session_expired",
                            message="Session expired or unknown session_token.",
                            path=None,
                        )
                    ],
                ),
                status_code=404,
            )

        raw_json = body.config_json.strip().encode()
        prev_token = old_session.session_token
        overrides, manual_maps = reconcile_pairs_from_optional_dict(body.reconcile_overrides)

        outcome = await run_loader_validation_pipeline(
            raw_json,
            old_session.api_key,
            old_session.org_id,
            reconcile_overrides=overrides,
            manual_mappings=manual_maps,
            prior_config=old_session.config,
        )
        if isinstance(outcome, LoaderValidationFailure):
            return loader_validation_failure_to_envelope(outcome)

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

        return loader_validation_success_to_v1_envelope(
            outcome,
            extra_data={"session_token": session.session_token},
        )

    @router.post(
        "/api/config/patch-json",
        tags=["agent"],
        response_model=LoaderSetupEnvelopeV1,
        response_model_exclude_none=True,
        responses={
            404: {"model": LoaderSetupEnvelopeV1},
            422: {"model": LoaderSetupEnvelopeV1},
        },
    )
    async def patch_json_config(request: Request) -> LoaderSetupEnvelopeV1 | JSONResponse:
        """Shallow-merge top-level keys into the session executable config, then revalidate (Plan 05).

        Same pipeline and § v1 envelope as ``POST /api/revalidate-json``; use when agents or UI
        submit partial top-level updates instead of a full ``config_json`` string.
        """
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return loader_setup_json_response(
                LoaderSetupEnvelopeV1(
                    ok=False,
                    errors=[
                        LoaderSetupErrorItem(
                            code="invalid_body",
                            message="Request body must be JSON.",
                            path=None,
                        )
                    ],
                ),
                status_code=422,
            )
        if not isinstance(payload, dict):
            return loader_setup_json_response(
                LoaderSetupEnvelopeV1(
                    ok=False,
                    errors=[
                        LoaderSetupErrorItem(
                            code="invalid_body",
                            message="Request body must be a JSON object.",
                            path=None,
                        )
                    ],
                ),
                status_code=422,
            )
        try:
            body = ApplyConfigPatchJsonRequestV1.model_validate(payload)
        except ValidationError as exc:
            return loader_setup_json_response(
                LoaderSetupEnvelopeV1(
                    ok=False,
                    errors=error_items_from_pydantic_validation(exc),
                ),
                status_code=422,
            )

        old_session = sessions.get(body.session_token)
        if not old_session:
            return loader_setup_json_response(
                LoaderSetupEnvelopeV1(
                    ok=False,
                    errors=[
                        LoaderSetupErrorItem(
                            code="session_expired",
                            message="Session expired or unknown session_token.",
                            path=None,
                        )
                    ],
                ),
                status_code=404,
            )

        base = session_working_config_dict(old_session)
        merged = {**base, **body.shallow_merge}
        for i, op in enumerate(body.pointer_sets):
            try:
                apply_json_pointer_set(merged, op.path, op.value)
            except (ValueError, KeyError, TypeError) as exc:
                return loader_setup_json_response(
                    LoaderSetupEnvelopeV1(
                        ok=False,
                        errors=[
                            LoaderSetupErrorItem(
                                code="pointer_set_failed",
                                message=f"{op.path}: {exc}",
                                path=f"pointer_sets[{i}]",
                            )
                        ],
                    ),
                    status_code=422,
                )
        raw_json = dumps_pretty(merged).encode("utf-8")
        prev_token = old_session.session_token
        overrides, manual_maps = reconcile_pairs_from_optional_dict(body.reconcile_overrides)

        outcome = await run_loader_validation_pipeline(
            raw_json,
            old_session.api_key,
            old_session.org_id,
            reconcile_overrides=overrides,
            manual_mappings=manual_maps,
            prior_config=old_session.config,
        )
        if isinstance(outcome, LoaderValidationFailure):
            return loader_validation_failure_to_envelope(outcome)

        session = apply_loader_validation_success_to_session(
            outcome,
            old_session.api_key,
            old_session.org_id,
            org_label=getattr(old_session, "org_label", None),
            generation_recipes=old_session.generation_recipes,
            working_config_json=None,
        )
        sessions[session.session_token] = session
        del sessions[prev_token]
        await persist_loader_draft(request, session)

        return loader_validation_success_to_v1_envelope(
            outcome,
            extra_data={"session_token": session.session_token},
        )
