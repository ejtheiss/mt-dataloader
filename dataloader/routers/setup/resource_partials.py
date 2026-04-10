"""HTMX partials: resource drawer, IA connection updates, JSON payload edits."""

from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from dataloader.helpers import UPDATABLE_RESOURCE_TYPES, build_available_connections
from dataloader.routers.deps import OptionalSessionQueryDep, SessionFormDep, TemplatesDep
from dataloader.routers.setup._helpers import find_resource_in_config, rereconcile_session
from dataloader.session import sessions
from jsonutil import loads_str


def register_resource_partials(router: APIRouter) -> None:
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

        rereconcile_session(sess)

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

        section_list, idx, resource = find_resource_in_config(
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
        rereconcile_session(session)

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
