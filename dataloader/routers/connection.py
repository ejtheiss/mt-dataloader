"""Connection management routes: schema export, editor, test, apply."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from modern_treasury import AsyncModernTreasury, AuthenticationError

from dataloader.routers.deps import OptionalSessionQueryDep, TemplatesDep
from models import DataLoaderConfig
from org import OrgRegistry, discover_org

router = APIRouter(tags=["connection"])


@router.get("/api/schema")
async def get_schema():
    """Export the full DataLoaderConfig JSON Schema."""
    return DataLoaderConfig.model_json_schema()


@router.get("/api/connection-editor", include_in_schema=False)
async def connection_editor(
    request: Request,
    templates: TemplatesDep,
    sess: OptionalSessionQueryDep,
):
    """Return the connection editor partial, pre-filled from session storage."""
    return templates.TemplateResponse(
        request,
        "partials/connection_editor.html",
        {
            "api_key": sess.api_key if sess else "",
            "org_id": sess.org_id if sess else "",
            "org_name": "",
        },
    )


@router.post("/api/connection-test", include_in_schema=False)
async def connection_test(request: Request):
    """Ping MT with provided credentials, return status HTML."""
    form = await request.form()
    api_key = str(form.get("api_key", ""))
    org_id = str(form.get("org_id", ""))

    if not api_key or not org_id:
        return HTMLResponse(
            '<div class="alert alert-warning">API key and org ID are required.</div>'
        )

    try:
        async with AsyncModernTreasury(api_key=api_key, organization_id=org_id) as client:
            await client.ping()
        return HTMLResponse('<div class="alert alert-success">Connection successful ✓</div>')
    except AuthenticationError:
        return HTMLResponse(
            '<div class="alert alert-error">Authentication failed — check API key and org ID.</div>'
        )
    except Exception as exc:
        return HTMLResponse(f'<div class="alert alert-error">Connection error: {exc}</div>')


@router.post("/api/connection-apply", include_in_schema=False)
async def connection_apply(
    request: Request,
    sess: OptionalSessionQueryDep,
):
    """Re-discover with new credentials without losing config/flow state."""
    form = await request.form()
    api_key = str(form.get("api_key", ""))
    org_id = str(form.get("org_id", ""))

    if not api_key or not org_id:
        return HTMLResponse(
            '<div class="alert alert-warning">API key and org ID are required.</div>'
        )

    try:
        async with AsyncModernTreasury(api_key=api_key, organization_id=org_id) as client:
            await client.ping()
            discovery = await discover_org(client, config=sess.config if sess else None)
            org_registry = OrgRegistry.from_discovery(discovery)

        if sess:
            sess.api_key = api_key
            sess.org_id = org_id
            sess.discovery = discovery
            sess.org_registry = org_registry
            return HTMLResponse(
                '<div class="alert alert-success">Connection updated ✓ — '
                f"discovered {len(org_registry.refs)} resources. "
                "Config and flows preserved.</div>"
            )

        return HTMLResponse(
            '<div class="alert alert-success">Connection verified ✓ — '
            f"discovered {len(org_registry.refs)} resources. "
            "No active session to update.</div>"
        )
    except AuthenticationError:
        return HTMLResponse(
            '<div class="alert alert-error">Authentication failed — credentials not changed.</div>'
        )
    except Exception as exc:
        return HTMLResponse(f'<div class="alert alert-error">Apply failed: {exc}</div>')
