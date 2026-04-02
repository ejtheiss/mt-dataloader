"""Setup routes: root redirect, setup page, validate, revalidate, validate-json, preview."""

from __future__ import annotations

import hashlib
import json
import secrets
from collections import Counter
from dataclasses import asdict, dataclass
from dataclasses import field as dc_field
from graphlib import CycleError

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from loguru import logger
from modern_treasury import (
    APIConnectionError,
    APITimeoutError,
    AsyncModernTreasury,
    AuthenticationError,
)
from pydantic import ValidationError

from dataloader.engine import (
    RefRegistry,
    all_resources,
    config_hash,
    dry_run,
    typed_ref_for,
)
from dataloader.routers.deps import OptionalSessionQueryDep, SessionFormDep, TemplatesDep
from flow_compiler import AuthoringConfig, compile_to_plan, flatten_actor_refs
from flow_validator import validate_flow
from helpers import (
    UPDATABLE_RESOURCE_TYPES,
    build_available_connections,
    build_discovered_by_type,
    build_discovered_id_lookup,
    build_flow_grouped_preview,
    build_preview,
    error_response,
    format_validation_errors,
)
from jsonutil import dumps_pretty, loads_str
from models import DataLoaderConfig, DisplayPhase
from org import (
    DiscoveryResult,
    OrgRegistry,
    discover_org,
    reconcile_config,
    sync_connection_entities_from_reconciliation,
)
from session import SessionState, prune_expired_sessions, sessions

router = APIRouter(tags=["setup"])


# ---------------------------------------------------------------------------
# Shared validation pipeline
# ---------------------------------------------------------------------------


@dataclass
class _PipelineResult:
    """Intermediate result from the shared validate/revalidate pipeline."""

    config: DataLoaderConfig
    config_json_text: str
    #: JSON of parsed config before ``compile_to_plan`` clears ``funds_flows`` (emit pass).
    authoring_config_json: str
    flow_irs: list
    expanded_flows: list
    mermaid_diagrams: list | None
    view_data_cache: list | None
    discovery: DiscoveryResult | None
    org_registry: OrgRegistry | None
    reconciliation: object | None
    registry: RefRegistry
    skip_refs: set = dc_field(default_factory=set)
    update_refs: dict = dc_field(default_factory=dict)
    batches: list = dc_field(default_factory=list)
    preview_items: list = dc_field(default_factory=list)
    #: Serialized ``FlowDiagnostic`` dicts from ``validate_flow`` (advisory).
    flow_diagnostics: list = dc_field(default_factory=list)


def _edited_resource_typed_refs(
    prior: DataLoaderConfig | None,
    config: DataLoaderConfig,
) -> set[str]:
    """Refs whose serialized resource payload changed (same ref, different body).

    Used on revalidate so reconciliation does not keep skipping resources the user
    edited in JSON (e.g. connection ``entity_id``).
    """
    if prior is None:
        return set()
    old_map = {typed_ref_for(r): r for r in all_resources(prior)}
    changed: set[str] = set()
    for r in all_resources(config):
        ref = typed_ref_for(r)
        old = old_map.get(ref)
        if old is None:
            continue
        if old.model_dump_json(exclude_none=True) != r.model_dump_json(exclude_none=True):
            changed.add(ref)
    return changed


async def _validate_pipeline(
    raw_json: bytes,
    api_key: str,
    org_id: str,
    *,
    reconcile_overrides: dict | None = None,
    manual_mappings: dict | None = None,
    prior_config: DataLoaderConfig | None = None,
) -> _PipelineResult | str:
    """Run the full validate pipeline: parse → compile → discover → reconcile → DAG.

    Returns a ``_PipelineResult`` on success or an error-message string on failure.
    """
    # 1. Parse config
    try:
        config = DataLoaderConfig.model_validate_json(raw_json)
    except ValidationError as e:
        structured = format_validation_errors(e)
        detail_lines = [f"• {err['path']}: {err['message']}" for err in structured]
        return "Config Validation Error\n" + ("\n".join(detail_lines) or str(e))

    authoring_config_json = config.model_dump_json(indent=2, exclude_none=True)

    # 2. Compile
    try:
        authoring = AuthoringConfig(
            config=config.model_copy(deep=True),
            json_text=raw_json.decode(),
            source_hash=hashlib.sha256(raw_json).hexdigest(),
        )
        plan = compile_to_plan(authoring)
        config = plan.config
        flow_irs = list(plan.flow_irs)
        expanded_flows = list(plan.expanded_flows)
        mermaid_diagrams = list(plan.mermaid_diagrams) if plan.mermaid_diagrams else None
        view_data_cache = list(plan.view_data) if plan.view_data else None
    except (ValueError, KeyError, NotImplementedError) as e:
        return f"Compiler Error\n{e}"

    flow_diag_dicts: list[dict] = []
    if len(flow_irs) != len(expanded_flows):
        logger.warning(
            "flow_irs / expanded_flows length mismatch: {} vs {}",
            len(flow_irs),
            len(expanded_flows),
        )
    for ir, fc in zip(flow_irs, expanded_flows):
        for d in validate_flow(ir, actor_refs=flatten_actor_refs(fc.actors)):
            flow_diag_dicts.append(asdict(d))
    if flow_diag_dicts:
        by_rule = Counter(d["rule_id"] for d in flow_diag_dicts)
        logger.debug(
            "Flow advisory diagnostics: {} finding(s) by_rule={}",
            len(flow_diag_dicts),
            dict(by_rule),
        )

    # 3. Discover org
    discovery: DiscoveryResult | None = None
    org_registry: OrgRegistry | None = None
    async with AsyncModernTreasury(api_key=api_key, organization_id=org_id) as client:
        try:
            await client.ping()
        except AuthenticationError:
            return "Authentication Error\nInvalid API key or org ID"
        try:
            discovery = await discover_org(client, config=config)
            org_registry = OrgRegistry.from_discovery(discovery)
        except (APIConnectionError, APITimeoutError) as exc:
            logger.warning("Discovery failed: {}", str(exc))

    # 4. Registry + reconciliation
    registry = RefRegistry()
    known_refs: set[str] = set()
    if org_registry is not None:
        known_refs = org_registry.seed_engine_registry(registry)

    reconciliation = None
    skip_refs: set[str] = set()
    if discovery is not None:
        reconciliation = reconcile_config(config, discovery)

        registered_refs: set[str] = set()
        overrides = reconcile_overrides or {}
        mappings = manual_mappings or {}
        force_new_refs = _edited_resource_typed_refs(prior_config, config)

        for m in reconciliation.matches:
            if m.config_ref in overrides:
                val = overrides[m.config_ref]
                if isinstance(val, dict):
                    m.use_existing = val.get("use_existing", True)
                    if "discovered_id" in val:
                        m.discovered_id = val["discovered_id"]
                else:
                    m.use_existing = bool(val)
            if m.config_ref in force_new_refs:
                m.use_existing = False
            if m.use_existing and m.config_ref not in registered_refs:
                registry.register_or_update(m.config_ref, m.discovered_id)
                skip_refs.add(m.config_ref)
                registered_refs.add(m.config_ref)
                for ck, cid in m.child_refs.items():
                    registry.register_or_update(f"{m.config_ref}.{ck}", cid)

        if mappings:
            disc_by_id = build_discovered_id_lookup(discovery)
            for config_ref, disc_id in mappings.items():
                if not disc_id or config_ref in registered_refs:
                    continue
                if disc_by_id.get(disc_id):
                    registry.register_or_update(config_ref, disc_id)
                    skip_refs.add(config_ref)
                    registered_refs.add(config_ref)
                    if config_ref in reconciliation.unmatched_config:
                        reconciliation.unmatched_config.remove(config_ref)

        sync_connection_entities_from_reconciliation(
            config,
            discovery,
            reconciliation,
            mappings,
        )

    # 5. DAG dry-run
    try:
        batches = dry_run(config, known_refs, skip_refs=skip_refs)
    except CycleError as e:
        return f"Cycle Error\nCircular dependency: {e}"
    except KeyError as e:
        return f"Reference Error\n{e}"

    # 6. Build preview
    resource_map = {typed_ref_for(r): r for r in all_resources(config)}
    preview_items = build_preview(
        batches,
        resource_map,
        skip_refs=skip_refs,
        reconciliation=reconciliation,
    )

    config_json_text = config.model_dump_json(indent=2, exclude_none=True)

    return _PipelineResult(
        config=config,
        config_json_text=config_json_text,
        authoring_config_json=authoring_config_json,
        flow_irs=flow_irs,
        expanded_flows=expanded_flows,
        mermaid_diagrams=mermaid_diagrams,
        view_data_cache=view_data_cache,
        discovery=discovery,
        org_registry=org_registry,
        reconciliation=reconciliation,
        registry=registry,
        skip_refs=skip_refs,
        batches=batches,
        preview_items=preview_items,
        flow_diagnostics=flow_diag_dicts,
    )


def _pipeline_result_to_session(
    result: _PipelineResult,
    api_key: str,
    org_id: str,
    *,
    org_label: str | None = None,
    working_config_json: str | None = None,
    generation_recipes: dict | None = None,
) -> SessionState:
    """Build a SessionState from a pipeline result."""
    token = secrets.token_urlsafe(32)
    return SessionState(
        session_token=token,
        api_key=api_key,
        org_id=org_id,
        config=result.config,
        config_json_text=result.config_json_text,
        registry=result.registry,
        batches=result.batches,
        preview_items=result.preview_items,
        org_registry=result.org_registry,
        discovery=result.discovery,
        reconciliation=result.reconciliation,
        skip_refs=result.skip_refs,
        flow_ir=result.flow_irs,
        expanded_flows=result.expanded_flows,
        pattern_flow_ir=result.flow_irs,
        pattern_expanded_flows=result.expanded_flows,
        base_config_json=result.config_json_text,
        authoring_config_json=result.authoring_config_json,
        mermaid_diagrams=result.mermaid_diagrams,
        view_data_cache=result.view_data_cache,
        working_config_json=working_config_json or result.config_json_text,
        generation_recipes=generation_recipes or {},
        org_label=org_label,
        flow_diagnostics=result.flow_diagnostics or None,
    )


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
async def setup_page(request: Request, templates: TemplatesDep):
    return templates.TemplateResponse(request, "setup.html", {"title": "Setup"})


@router.post("/api/config/save")
async def save_config(request: Request):
    """Write edited config JSON back to the session and optionally to disk."""
    try:
        body = await request.json()
    except Exception:
        return {"status": "error", "detail": "Invalid request body"}

    session_token = body.get("session_token", "")
    config_json = body.get("config_json", "")

    session = sessions.get(session_token)
    if not session:
        return {"status": "error", "detail": "Session expired"}

    try:
        json.loads(config_json)
    except json.JSONDecodeError as exc:
        return {"status": "error", "detail": f"Invalid JSON: {exc}"}

    try:
        config = DataLoaderConfig.model_validate_json(config_json.encode())
    except ValidationError as exc:
        structured = format_validation_errors(exc)
        return {"status": "error", "detail": structured[0]["message"] if structured else str(exc)}

    session.config = config
    session.config_json_text = dumps_pretty(json.loads(config_json))
    session.working_config_json = session.config_json_text

    return {"status": "ok", "message": "Config saved to session"}


@router.post("/api/validate-json")
async def validate_json(request: Request):
    """Programmatic JSON validation endpoint for LLM repair loops."""
    try:
        body = await request.body()
        config = DataLoaderConfig.model_validate_json(body)
    except ValidationError as e:
        return {"valid": False, "errors": format_validation_errors(e)}

    had_funds_flows = bool(config.funds_flows)

    try:
        from flow_compiler import (
            pass_compile_to_ir,
            pass_emit_resources,
            pass_expand_instances,
        )

        authoring = AuthoringConfig(
            config=config.model_copy(deep=True),
            json_text=body.decode(),
            source_hash=hashlib.sha256(body).hexdigest(),
        )
        plan = compile_to_plan(
            authoring,
            pipeline=(pass_expand_instances, pass_compile_to_ir, pass_emit_resources),
        )
        config = plan.config
    except (ValueError, KeyError, NotImplementedError) as e:
        return {
            "valid": False,
            "errors": [{"path": "(compiler)", "type": "compile_error", "message": str(e)}],
        }

    try:
        batches = dry_run(config)
    except CycleError as e:
        return {
            "valid": False,
            "errors": [{"path": "(dag)", "type": "cycle_error", "message": str(e)}],
        }
    except KeyError as e:
        return {
            "valid": False,
            "errors": [{"path": "(dag)", "type": "unresolvable_ref", "message": str(e)}],
        }

    return {
        "valid": True,
        "resource_count": sum(len(b) for b in batches),
        "batch_count": len(batches),
        "has_funds_flows": had_funds_flows,
        "errors": [],
    }


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

    result = await _validate_pipeline(raw_json, api_key, org_id)
    if isinstance(result, str):
        return _pipeline_error_response(result)

    ol = org_name.strip() or None
    session = _pipeline_result_to_session(result, api_key, org_id, org_label=ol)
    sessions[session.session_token] = session
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

    result = await _validate_pipeline(
        raw_json,
        old_session.api_key,
        old_session.org_id,
        reconcile_overrides=overrides,
        manual_mappings=manual_maps,
        prior_config=old_session.config,
    )
    if isinstance(result, str):
        return _pipeline_error_response(result)

    session = _pipeline_result_to_session(
        result,
        old_session.api_key,
        old_session.org_id,
        org_label=getattr(old_session, "org_label", None),
        generation_recipes=old_session.generation_recipes,
        working_config_json=old_session.working_config_json,
    )
    sessions[session.session_token] = session
    del sessions[prev_token]

    return _render_preview_or_redirect(request, session, templates)


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
