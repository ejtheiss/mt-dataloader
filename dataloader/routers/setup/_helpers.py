"""Shared helpers for setup routes (split from former monolithic setup.py)."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

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
    build_preview,
    error_response,
)
from dataloader.loader_validation import (
    LoaderValidationFailure,
    loader_validation_failure_htmx_parts,
)
from dataloader.session import SessionState
from jsonutil import loads_str
from models import DataLoaderConfig, DisplayPhase
from models.loader_setup_json import LoaderSetupEnvelopeV1
from org import reconcile_config, sync_connection_entities_from_reconciliation


def loader_setup_json_response(
    envelope: LoaderSetupEnvelopeV1,
    status_code: int = 200,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=envelope.model_dump(mode="json", exclude_none=True),
    )


def reconcile_pairs_from_optional_dict(raw: dict | None) -> tuple[dict, dict]:
    """Match HTMX ``reconcile_overrides`` JSON semantics (flat or nested)."""
    if not raw or not isinstance(raw, dict):
        return {}, {}
    overrides = raw.get("overrides", raw)
    if not isinstance(overrides, dict):
        overrides = {}
    mm = raw.get("manual_mappings")
    manual_maps = dict(mm) if isinstance(mm, dict) else {}
    return overrides, manual_maps


def session_working_config_dict(session: SessionState) -> dict:
    """Executable config as a plain dict for Plan 05 shallow merge (patch-json)."""
    text = (session.working_config_json or session.config_json_text or "").strip()
    if text:
        try:
            parsed = loads_str(text)
            if isinstance(parsed, dict):
                return dict(parsed)
        except (TypeError, ValueError):
            pass
    return session.config.model_dump(mode="json", exclude_none=True)


def pipeline_error_response(outcome: LoaderValidationFailure) -> HTMLResponse:
    """HTMX errors from the same typed model as § v1 JSON (plan 05)."""
    title, detail = loader_validation_failure_htmx_parts(outcome)
    return error_response(title, detail)


def render_preview_or_redirect(
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


def rereconcile_session(session: SessionState) -> None:
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


def find_resource_in_config(
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
