"""Setup routes: root redirect, setup page, validate, revalidate, validate-json, preview."""

from __future__ import annotations

import hashlib
import json
import secrets

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from graphlib import CycleError
from loguru import logger
from modern_treasury import (
    APIConnectionError,
    APITimeoutError,
    AsyncModernTreasury,
    AuthenticationError,
)
from pydantic import ValidationError

from engine import (
    RefRegistry,
    all_resources,
    config_hash,
    dry_run,
    typed_ref_for,
)
from flow_compiler import AuthoringConfig, compile_to_plan
from helpers import (
    build_discovered_by_type,
    build_discovered_id_lookup,
    build_flow_grouped_preview,
    build_preview,
    error_response,
    format_validation_errors,
    get_templates,
)
from models import DataLoaderConfig, DisplayPhase
from org import DiscoveryResult, OrgRegistry, reconcile_config, discover_org
from session import SessionState, sessions, prune_expired_sessions

router = APIRouter(tags=["setup"])


@router.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/setup")


@router.get("/setup", include_in_schema=False)
async def setup_page(request: Request):
    templates = get_templates()
    return templates.TemplateResponse(request, "setup.html", {"title": "Setup"})


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
            pass_expand_instances, pass_compile_to_ir, pass_emit_resources,
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
        return {"valid": False, "errors": [
            {"path": "(compiler)", "type": "compile_error", "message": str(e)}
        ]}

    try:
        batches = dry_run(config)
    except CycleError as e:
        return {"valid": False, "errors": [
            {"path": "(dag)", "type": "cycle_error", "message": str(e)}
        ]}
    except KeyError as e:
        return {"valid": False, "errors": [
            {"path": "(dag)", "type": "unresolvable_ref", "message": str(e)}
        ]}

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
    api_key: str = Form(...),
    org_id: str = Form(...),
    config_file: UploadFile | None = File(None),
    config_json: str | None = Form(None),
):
    """Validate API key, discover org, parse config, compile, compute DAG, cache state."""
    templates = get_templates()
    prune_expired_sessions()

    if config_json and config_json.strip():
        raw_json = config_json.strip().encode()
    elif config_file and config_file.size:
        raw_json = await config_file.read()
    else:
        return error_response("Missing Config", "Upload a JSON file or paste JSON directly.")

    try:
        config = DataLoaderConfig.model_validate_json(raw_json)
    except ValidationError as e:
        structured = format_validation_errors(e)
        detail_lines = [f"• {err['path']}: {err['message']}" for err in structured]
        return error_response(
            "Config Validation Error",
            "\n".join(detail_lines) or str(e),
        )

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
        return error_response("Compiler Error", str(e))

    async with AsyncModernTreasury(
        api_key=api_key, organization_id=org_id
    ) as client:
        try:
            await client.ping()
        except AuthenticationError:
            return error_response(
                "Authentication Error", "Invalid API key or org ID"
            )

        discovery: DiscoveryResult | None = None
        org_registry: OrgRegistry | None = None
        try:
            discovery = await discover_org(client, config=config)
            org_registry = OrgRegistry.from_discovery(discovery)
        except (APIConnectionError, APITimeoutError) as exc:
            logger.warning("Discovery failed: {}", str(exc))

    registry = RefRegistry()
    known_refs: set[str] = set()
    if org_registry is not None:
        known_refs = org_registry.seed_engine_registry(registry)

    reconciliation = None
    skip_refs: set[str] = set()
    if discovery is not None:
        reconciliation = reconcile_config(config, discovery)
        for m in reconciliation.matches:
            if m.use_existing:
                registry.register_or_update(m.config_ref, m.discovered_id)
                skip_refs.add(m.config_ref)

    try:
        batches = dry_run(config, known_refs, skip_refs=skip_refs)
    except CycleError as e:
        return error_response("Cycle Error", f"Circular dependency: {e}")
    except KeyError as e:
        return error_response("Reference Error", str(e))

    resource_map = {typed_ref_for(r): r for r in all_resources(config)}
    preview_items = build_preview(batches, resource_map)

    config_json_text = json.dumps(
        json.loads(raw_json), indent=2, ensure_ascii=False
    )
    working_config_json = config_json_text

    token = secrets.token_urlsafe(32)
    sessions[token] = SessionState(
        session_token=token,
        api_key=api_key,
        org_id=org_id,
        config=config,
        config_json_text=config_json_text,
        registry=registry,
        batches=batches,
        preview_items=preview_items,
        org_registry=org_registry,
        discovery=discovery,
        reconciliation=reconciliation,
        skip_refs=skip_refs,
        flow_ir=flow_irs,
        expanded_flows=expanded_flows,
        pattern_flow_ir=flow_irs,
        pattern_expanded_flows=expanded_flows,
        mermaid_diagrams=mermaid_diagrams,
        view_data_cache=view_data_cache,
        working_config_json=working_config_json,
    )

    had_funds_flows = bool(flow_irs)
    if had_funds_flows:
        resp = HTMLResponse(content="", status_code=200)
        resp.headers["HX-Redirect"] = f"/flows?session_token={token}"
        return resp

    return templates.TemplateResponse(
        request,
        "preview.html",
        {
            "session_token": token,
            "batches": batches,
            "preview_items": preview_items,
            "config_hash": config_hash(config),
            "resource_count": sum(len(b) for b in batches),
            "deletable_count": sum(
                1
                for item in preview_items
                if item["deletable"]
            ),
            "non_deletable_count": sum(
                1
                for item in preview_items
                if not item["deletable"]
            ),
            "display_phases": DisplayPhase,
            "discovery": discovery,
            "config_json_text": config_json_text,
            "discovered_by_type": build_discovered_by_type(discovery),
        },
    )


@router.post("/api/revalidate")
async def revalidate(
    request: Request,
    session_token: str = Form(...),
    config_json: str = Form(...),
    reconcile_overrides: str | None = Form(None),
):
    """Re-validate edited JSON using credentials from an existing session."""
    templates = get_templates()
    session = sessions.get(session_token)
    if not session:
        return error_response("Session Expired", "Please start over from Setup.")

    raw_json = config_json.strip().encode()
    try:
        config = DataLoaderConfig.model_validate_json(raw_json)
    except ValidationError as e:
        structured = format_validation_errors(e)
        detail_lines = [f"• {err['path']}: {err['message']}" for err in structured]
        return error_response(
            "Config Validation Error",
            "\n".join(detail_lines) or str(e),
        )

    try:
        authoring = AuthoringConfig(
            config=config.model_copy(deep=True),
            json_text=raw_json.decode(),
            source_hash=hashlib.sha256(raw_json).hexdigest(),
        )
        plan = compile_to_plan(authoring)
        config = plan.config
        flow_irs_reval = list(plan.flow_irs)
        expanded_flows_reval = list(plan.expanded_flows)
        mermaid_diagrams_reval = list(plan.mermaid_diagrams) if plan.mermaid_diagrams else None
        view_data_reval = list(plan.view_data) if plan.view_data else None
    except (ValueError, KeyError, NotImplementedError) as e:
        return error_response("Compiler Error", str(e))

    async with AsyncModernTreasury(
        api_key=session.api_key, organization_id=session.org_id
    ) as client:
        discovery: DiscoveryResult | None = None
        org_registry: OrgRegistry | None = None
        try:
            discovery = await discover_org(client, config=config)
            org_registry = OrgRegistry.from_discovery(discovery)
        except (APIConnectionError, APITimeoutError) as exc:
            logger.warning("Discovery failed during revalidate: {}", str(exc))

    registry = RefRegistry()
    known_refs: set[str] = set()
    if org_registry is not None:
        known_refs = org_registry.seed_engine_registry(registry)

    reconciliation = None
    skip_refs: set[str] = set()
    if discovery is not None:
        reconciliation = reconcile_config(config, discovery)
        overrides: dict[str, bool | dict] = {}
        manual_mappings: dict[str, str] = {}
        if reconcile_overrides:
            try:
                raw_ov = json.loads(reconcile_overrides)
                overrides = raw_ov.get("overrides", raw_ov) if isinstance(raw_ov, dict) else {}
                if isinstance(raw_ov, dict):
                    manual_mappings = raw_ov.get("manual_mappings", {})
            except json.JSONDecodeError:
                pass

        registered_refs: set[str] = set()
        for m in reconciliation.matches:
            if m.config_ref in overrides:
                val = overrides[m.config_ref]
                if isinstance(val, dict):
                    m.use_existing = val.get("use_existing", True)
                    if "discovered_id" in val:
                        m.discovered_id = val["discovered_id"]
                else:
                    m.use_existing = bool(val)
            if m.use_existing and m.config_ref not in registered_refs:
                registry.register_or_update(m.config_ref, m.discovered_id)
                skip_refs.add(m.config_ref)
                registered_refs.add(m.config_ref)

        if manual_mappings and discovery is not None:
            disc_by_id = build_discovered_id_lookup(discovery)
            for config_ref, disc_id in manual_mappings.items():
                if not disc_id or config_ref in registered_refs:
                    continue
                disc_info = disc_by_id.get(disc_id)
                if disc_info:
                    registry.register_or_update(config_ref, disc_id)
                    skip_refs.add(config_ref)
                    registered_refs.add(config_ref)
                    if config_ref in reconciliation.unmatched_config:
                        reconciliation.unmatched_config.remove(config_ref)

    try:
        batches = dry_run(config, known_refs, skip_refs=skip_refs)
    except CycleError as e:
        return error_response("Cycle Error", f"Circular dependency: {e}")
    except KeyError as e:
        return error_response("Reference Error", str(e))

    resource_map = {typed_ref_for(r): r for r in all_resources(config)}
    preview_items = build_preview(batches, resource_map)

    config_json_text = json.dumps(
        json.loads(raw_json), indent=2, ensure_ascii=False
    )

    new_token = secrets.token_urlsafe(32)
    sessions[new_token] = SessionState(
        session_token=new_token,
        api_key=session.api_key,
        org_id=session.org_id,
        config=config,
        config_json_text=config_json_text,
        registry=registry,
        batches=batches,
        preview_items=preview_items,
        org_registry=org_registry,
        discovery=discovery,
        reconciliation=reconciliation,
        skip_refs=skip_refs,
        flow_ir=flow_irs_reval,
        expanded_flows=expanded_flows_reval,
        pattern_flow_ir=flow_irs_reval,
        pattern_expanded_flows=expanded_flows_reval,
        mermaid_diagrams=mermaid_diagrams_reval,
        view_data_cache=view_data_reval,
        working_config_json=session.working_config_json,
        generation_recipe=session.generation_recipe,
    )

    del sessions[session_token]

    if flow_irs_reval:
        resp = HTMLResponse(content="", status_code=200)
        resp.headers["HX-Redirect"] = f"/flows?session_token={new_token}"
        return resp

    return templates.TemplateResponse(
        request,
        "preview.html",
        {
            "session_token": new_token,
            "batches": batches,
            "preview_items": preview_items,
            "config_hash": config_hash(config),
            "resource_count": sum(len(b) for b in batches),
            "deletable_count": sum(
                1 for item in preview_items if item["deletable"]
            ),
            "non_deletable_count": sum(
                1 for item in preview_items if not item["deletable"]
            ),
            "display_phases": DisplayPhase,
            "discovery": discovery,
            "reconciliation": reconciliation,
            "config_json_text": config_json_text,
            "discovered_by_type": build_discovered_by_type(discovery),
            "has_funds_flows": bool(flow_irs_reval),
        },
    )


@router.get("/preview", include_in_schema=False)
async def preview_page(request: Request):
    """Preview page — flow-grouped when funds_flows present, flat otherwise."""
    templates = get_templates()
    session_token = request.query_params.get("session_token", "")
    session = sessions.get(session_token)
    if not session:
        return RedirectResponse(url="/setup")

    total_resources = sum(len(b) for b in session.batches)
    deletable_count = sum(1 for i in session.preview_items if i["deletable"])
    non_deletable_count = sum(1 for i in session.preview_items if not i["deletable"])

    if session.flow_ir:
        flow_groups = build_flow_grouped_preview(session)
        return templates.TemplateResponse(
            request,
            "preview_flows_page.html",
            {
                "session_token": session_token,
                "flow_groups": flow_groups,
                "resource_count": total_resources,
                "deletable_count": deletable_count,
                "non_deletable_count": non_deletable_count,
                "discovery": session.discovery,
                "reconciliation": session.reconciliation,
                "config_json_text": session.config_json_text,
                "has_funds_flows": True,
                "mermaid_diagrams": session.mermaid_diagrams or [],
            },
        )

    return templates.TemplateResponse(
        request,
        "preview_page.html",
        {
            "session_token": session_token,
            "batches": session.batches,
            "preview_items": session.preview_items,
            "config_hash": config_hash(session.config),
            "resource_count": total_resources,
            "deletable_count": deletable_count,
            "non_deletable_count": non_deletable_count,
            "display_phases": DisplayPhase,
            "discovery": session.discovery,
            "reconciliation": session.reconciliation,
            "config_json_text": session.config_json_text,
            "discovered_by_type": build_discovered_by_type(session.discovery),
            "has_funds_flows": False,
        },
    )
