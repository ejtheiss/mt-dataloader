"""Setup routes: root redirect, setup page, validate, revalidate, validate-json, preview."""

from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError

from dataloader.engine import (
    all_resources,
    config_hash,
    dry_run,
    typed_ref_for,
)
from dataloader.helpers import (
    UPDATABLE_RESOURCE_TYPES,
    build_available_connections,
    build_discovered_by_type,
    build_flow_grouped_preview,
    build_preview,
    error_response,
)
from dataloader.loader_validation import (
    LoaderValidationFailure,
    apply_loader_validation_success_to_session,
    run_headless_validate_json,
    run_loader_validation_pipeline,
)
from dataloader.routers.deps import (
    AsyncSessionDep,
    OptionalSessionQueryDep,
    SessionFormDep,
    TemplatesDep,
)
from dataloader.session import SessionState, prune_expired_sessions, sessions
from dataloader.session.draft_persist import (
    merge_loader_draft_into_session,
    persist_loader_draft,
    run_access_context_for_request,
)
from db.repositories import loader_drafts as drafts_repo
from jsonutil import dumps_pretty, loads_str
from models import DataLoaderConfig, DisplayPhase
from models.loader_setup_json import (
    LoaderSetupEnvelopeV1,
    LoaderSetupErrorItem,
    error_items_from_pydantic_validation,
    parse_request_json_body,
)
from org import reconcile_config, sync_connection_entities_from_reconciliation

router = APIRouter(tags=["setup"])


# Full validate pipeline: ``run_loader_validation_pipeline`` → ``apply_loader_validation_success_to_session``
# in ``dataloader.loader_validation`` (Wave D: validate without Request/DB; router applies + persists).


def _pipeline_error_response(message: str):
    title, _, detail = message.partition("\n")
    return error_response(title, detail)


def _render_preview_or_redirect(
    request: Request,
    session: SessionState,
    templates,
) -> HTMLResponse:
    """Return an HX-Redirect to /flows or render the preview page."""
    if session.flow_ir:
        resp = HTMLResponse(content="", status_code=200)
        resp.headers["HX-Redirect"] = f"/flows?session_token={session.session_token}"
        return resp

    return templates.TemplateResponse(
        request,
        "preview.html",
        {
            "session_token": session.session_token,
            "batches": session.batches,
            "preview_items": session.preview_items,
            "config_hash": config_hash(session.config),
            "resource_count": sum(len(b) for b in session.batches),
            "deletable_count": sum(1 for i in session.preview_items if i["deletable"]),
            "non_deletable_count": sum(1 for i in session.preview_items if not i["deletable"]),
            "display_phases": DisplayPhase,
            "discovery": session.discovery,
            "reconciliation": session.reconciliation,
            "config_json_text": session.config_json_text,
            "discovered_by_type": build_discovered_by_type(session.discovery),
            "has_funds_flows": bool(session.flow_ir),
            "available_connections": build_available_connections(
                session.config,
                session.discovery,
            ),
        },
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


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


@router.post("/api/config/save")
async def save_config(request: Request):
    """Write edited config JSON back to the session and optionally to disk (JSON API v1 envelope)."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            status_code=422,
            content=LoaderSetupEnvelopeV1(
                ok=False,
                errors=[
                    LoaderSetupErrorItem(
                        code="invalid_body",
                        message="Request body must be JSON with session_token and config_json.",
                        path=None,
                    )
                ],
            ).json_response_dict(),
        )

    if not isinstance(body, dict):
        return JSONResponse(
            status_code=422,
            content=LoaderSetupEnvelopeV1(
                ok=False,
                errors=[
                    LoaderSetupErrorItem(
                        code="invalid_body",
                        message="Request body must be a JSON object.",
                        path=None,
                    )
                ],
            ).json_response_dict(),
        )

    session_token = body.get("session_token", "")
    config_json = body.get("config_json", "")

    session = sessions.get(session_token)
    if not session:
        return JSONResponse(
            status_code=404,
            content=LoaderSetupEnvelopeV1(
                ok=False,
                errors=[
                    LoaderSetupErrorItem(
                        code="session_expired",
                        message="Session expired or unknown session_token.",
                        path=None,
                    )
                ],
            ).json_response_dict(),
        )

    try:
        json.loads(config_json)
    except json.JSONDecodeError as exc:
        return JSONResponse(
            status_code=200,
            content=LoaderSetupEnvelopeV1(
                ok=False,
                phase="parse",
                errors=[
                    LoaderSetupErrorItem(
                        code="invalid_body",
                        message=f"config_json is not valid JSON: {exc}",
                        path=None,
                    )
                ],
            ).json_response_dict(),
        )

    try:
        config = DataLoaderConfig.model_validate_json(config_json.encode())
    except ValidationError as exc:
        return JSONResponse(
            status_code=200,
            content=LoaderSetupEnvelopeV1(
                ok=False,
                phase="parse",
                errors=error_items_from_pydantic_validation(exc),
            ).json_response_dict(),
        )

    session.config = config
    session.config_json_text = dumps_pretty(json.loads(config_json))
    session.working_config_json = session.config_json_text

    await persist_loader_draft(request, session)

    return JSONResponse(
        status_code=200,
        content=LoaderSetupEnvelopeV1(
            ok=True,
            phase="complete",
            data={"message": "Config saved to session"},
        ).json_response_dict(),
    )


@router.post("/api/validate-json")
async def validate_json(request: Request):
    """Programmatic JSON validation endpoint for LLM repair loops (JSON API v1 envelope)."""
    body = await request.body()
    if parse_request_json_body(body) is None:
        return JSONResponse(
            status_code=422,
            content=LoaderSetupEnvelopeV1(
                ok=False,
                errors=[
                    LoaderSetupErrorItem(
                        code="invalid_body",
                        message="Body must be UTF-8 JSON object (DataLoaderConfig).",
                        path=None,
                    )
                ],
            ).json_response_dict(),
        )

    outcome = run_headless_validate_json(body)
    return JSONResponse(
        status_code=200,
        content=LoaderSetupEnvelopeV1(
            ok=outcome.ok,
            phase=outcome.phase,
            errors=outcome.errors,
            data=outcome.data,
        ).json_response_dict(),
    )


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
        return _pipeline_error_response(outcome.message)

    ol = org_name.strip() or None
    session = apply_loader_validation_success_to_session(outcome, api_key, org_id, org_label=ol)
    sessions[session.session_token] = session
    await persist_loader_draft(request, session)
    return _render_preview_or_redirect(request, session, templates)


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
        return _pipeline_error_response(outcome.message)

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
    return _render_preview_or_redirect(request, session, templates)


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
        return _pipeline_error_response(outcome.message)

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
    return _render_preview_or_redirect(request, session, templates)


@router.post("/api/draft/discard", include_in_schema=False)
async def discard_loader_draft(request: Request, db_session: AsyncSessionDep):
    """Explicitly remove the durable draft for the current app user (runs unchanged)."""
    ctx = run_access_context_for_request(request)
    await drafts_repo.delete_loader_draft(db_session, ctx.user_id, ctx)
    await db_session.commit()
    return RedirectResponse(url="/setup", status_code=303)


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


@router.get("/api/resource-detail", include_in_schema=False)
async def resource_detail_drawer(
    request: Request,
    templates: TemplatesDep,
    sess: OptionalSessionQueryDep,
):
    """Return a drawer-friendly KV table for a single resource."""
    typed_ref = request.query_params.get("ref", "")
    if not sess:
        return HTMLResponse("<p>Session expired</p>", status_code=404)

    session_token = sess.session_token

    item = next((i for i in sess.preview_items if i["typed_ref"] == typed_ref), None)
    if not item:
        return HTMLResponse(f"<p>Resource not found: {typed_ref}</p>", status_code=404)

    return templates.TemplateResponse(
        request,
        "partials/resource_drawer.html",
        {"item": item, "session_token": session_token},
    )


@router.post("/api/update-ia-connection", include_in_schema=False)
async def update_ia_connection(
    request: Request,
    templates: TemplatesDep,
    sess: SessionFormDep,
):
    """Change an internal account's connection_id, re-reconcile, rebuild preview."""
    form = await request.form()
    typed_ref = form.get("typed_ref", "")
    new_conn = form.get(f"connection_for_{typed_ref}", "")
    if not sess:
        return HTMLResponse("<p>Session expired</p>", status_code=404)

    session_token = sess.session_token

    ia_ref = typed_ref.split(".", 1)[1] if "." in typed_ref else typed_ref

    updated = False
    for ia in sess.config.internal_accounts:
        if ia.ref == ia_ref:
            ia.connection_id = new_conn
            updated = True
            break

    if not updated:
        return HTMLResponse(f"<p>IA not found: {typed_ref}</p>", status_code=404)

    sess.config_json_text = sess.config.model_dump_json(
        indent=2,
        exclude_none=True,
    )
    if sess.working_config_json is not None:
        sess.working_config_json = sess.config_json_text

    _rereconcile_session(sess)

    available_connections = build_available_connections(
        sess.config,
        sess.discovery,
    )

    return templates.TemplateResponse(
        request,
        "partials/resource_table.html",
        {
            "session_token": session_token,
            "preview_items": sess.preview_items,
            "available_connections": available_connections,
        },
    )


def _rereconcile_session(session: SessionState) -> None:
    """Re-run reconciliation, rebuild skip_refs/update_refs/batches/preview.

    Called after any change that affects reconciliation state (connection
    change, payload edit).
    """
    skip_refs: set[str] = set()
    update_refs: dict[str, str] = {}

    if session.discovery is not None:
        recon = reconcile_config(session.config, session.discovery)
        session.reconciliation = recon

        for m in recon.matches:
            if m.use_existing:
                session.registry.register_or_update(m.config_ref, m.discovered_id)
                skip_refs.add(m.config_ref)
                for ck, cid in m.child_refs.items():
                    session.registry.register_or_update(f"{m.config_ref}.{ck}", cid)

        for tref in session.payload_overrides:
            if tref not in skip_refs:
                continue
            rtype = tref.split(".", 1)[0] if "." in tref else ""
            match = next(
                (m for m in recon.matches if m.config_ref == tref),
                None,
            )
            if match is None:
                continue
            if rtype in UPDATABLE_RESOURCE_TYPES:
                skip_refs.discard(tref)
                update_refs[tref] = match.discovered_id
            else:
                skip_refs.discard(tref)
                session.registry.unregister(tref)
                for ck in match.child_refs:
                    session.registry.unregister(f"{tref}.{ck}")

        sync_connection_entities_from_reconciliation(
            session.config,
            session.discovery,
            recon,
            {},
        )

    session.skip_refs = skip_refs
    session.update_refs = update_refs

    try:
        known = set(session.org_registry.refs.keys()) if session.org_registry else None
        batches = dry_run(session.config, known, skip_refs=skip_refs)
    except Exception:
        batches = session.batches

    resource_map = {typed_ref_for(r): r for r in all_resources(session.config)}
    session.batches = batches
    session.preview_items = build_preview(
        batches,
        resource_map,
        skip_refs=skip_refs,
        reconciliation=session.reconciliation,
        update_refs=update_refs,
    )

    session.config_json_text = session.config.model_dump_json(
        indent=2,
        exclude_none=True,
    )
    if session.working_config_json is not None:
        session.working_config_json = session.config_json_text


def _find_resource_in_config(
    config: DataLoaderConfig,
    resource_type: str,
    ref: str,
):
    """Locate a resource object in a DataLoaderConfig by resource_type and ref.

    Returns ``(section_list, index, resource)`` or ``(None, -1, None)``.
    """
    for field_name in type(config).model_fields:
        items = getattr(config, field_name)
        if not isinstance(items, list):
            continue
        for idx, item in enumerate(items):
            rt = getattr(item, "resource_type", None)
            if rt == resource_type and getattr(item, "ref", None) == ref:
                return items, idx, item
    return None, -1, None


@router.post("/api/update-resource-payload", include_in_schema=False)
async def update_resource_payload(request: Request):
    """Accept an edited JSON payload for a resource and apply it back.

    If the resource was previously reconciled, returns a warning
    indicating whether the resource will now be updated in place
    (updatable types) or created new (non-updatable types).
    """
    try:
        body = await request.json()
    except Exception:
        return {"status": "error", "detail": "Invalid request body"}

    session_token = body.get("session_token", "")
    typed_ref = body.get("typed_ref", "")
    payload_str = body.get("payload", "")

    session = sessions.get(session_token)
    if not session:
        return {"status": "error", "detail": "Session expired"}

    if "." not in typed_ref:
        return {"status": "error", "detail": f"Invalid typed_ref: {typed_ref}"}

    resource_type, ref = typed_ref.split(".", 1)

    try:
        payload = loads_str(payload_str) if isinstance(payload_str, str) else payload_str
    except json.JSONDecodeError as exc:
        return {"status": "error", "detail": f"Invalid JSON: {exc}"}

    section_list, idx, resource = _find_resource_in_config(
        session.config,
        resource_type,
        ref,
    )
    if resource is None:
        return {"status": "error", "detail": f"Resource not found: {typed_ref}"}

    was_reconciled = typed_ref in session.skip_refs or typed_ref in session.update_refs

    payload["ref"] = ref

    model_cls = type(resource)
    try:
        updated = model_cls.model_validate(payload)
    except ValidationError as exc:
        errors = "; ".join(
            f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in exc.errors()
        )
        return {"status": "error", "detail": f"Validation failed: {errors}"}

    section_list[idx] = updated

    session.config_json_text = session.config.model_dump_json(
        indent=2,
        exclude_none=True,
    )
    if session.working_config_json is not None:
        session.working_config_json = session.config_json_text

    session.payload_overrides.add(typed_ref)
    _rereconcile_session(session)

    result: dict[str, str] = {"status": "ok"}
    if was_reconciled:
        if typed_ref in session.update_refs:
            result["warning"] = "will_update"
            result["detail"] = (
                "This resource was matched to an existing resource. "
                "It will be updated instead of skipped."
            )
        elif resource_type not in UPDATABLE_RESOURCE_TYPES:
            result["warning"] = "unreconciled"
            result["detail"] = (
                "This resource was matched to an existing resource. "
                "Editing the payload means it will be created new."
            )
        else:
            result["warning"] = "will_update"
            result["detail"] = (
                "This resource was matched to an existing resource. "
                "It will be updated instead of skipped."
            )

    return result
