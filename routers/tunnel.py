"""Tunnel management and MT webhook registration API routes.

Endpoints:
- POST /api/tunnel/start          Start ngrok tunnel
- POST /api/tunnel/stop           Stop ngrok tunnel
- GET  /api/tunnel/status         Poll tunnel health
- POST /api/tunnel/register-webhook  Auto-register webhook endpoint in MT
"""

from __future__ import annotations

import os

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse
from loguru import logger

import httpx

import ngrok_cloud
from tunnel import NgrokStartError

from mt_webhook_endpoints import (
    analyze_org_webhook_listeners,
    create_webhook_endpoint,
    list_webhook_endpoints,
    normalize_webhook_url,
    patch_webhook_endpoint,
)

router = APIRouter()

WEBHOOK_PATH = "/webhooks/mt"


@router.post("/api/tunnel/start", include_in_schema=False)
async def tunnel_start(
    request: Request,
    authtoken: str = Form(...),
    domain: str = Form(""),
):
    """Start the ngrok tunnel.  Persists authtoken to runs/.tunnel_config.json."""
    mgr = request.app.state.tunnel
    try:
        url = mgr.start(
            authtoken=authtoken.strip(),
            domain=domain.strip() or None,
        )
        return {"ok": True, "url": url}
    except NgrokStartError as exc:
        logger.warning("Tunnel start failed: {}", exc)
        return JSONResponse(
            {
                "ok": False,
                "error": str(exc),
                "ngrok_error": exc.code,
                "hint": exc.hint,
            },
            status_code=500,
        )
    except Exception as exc:
        logger.warning("Tunnel start failed: {}", exc)
        mgr.record_unknown_start_failure(str(exc))
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.post("/api/tunnel/stop", include_in_schema=False)
async def tunnel_stop(request: Request):
    """Stop the ngrok tunnel."""
    mgr = request.app.state.tunnel
    mgr.stop()
    return {"ok": True}


@router.get("/api/tunnel/status", include_in_schema=False)
async def tunnel_status(request: Request):
    """Return current tunnel connectivity and URL.

    The tunnel is **process-scoped** (one ngrok per dataloader instance); it does
    not depend on which MT org the user selects in the UI.
    """
    mgr = request.app.state.tunnel
    status = mgr.get_status()
    status["webhook_endpoint_id"] = mgr.saved_webhook_endpoint_id
    status["has_authtoken"] = bool(
        request.app.state.settings.ngrok_authtoken or mgr.saved_authtoken
    )
    fail = mgr.last_ngrok_failure()
    if fail and not status.get("connected"):
        status["ngrok_issue"] = fail
    else:
        status["ngrok_issue"] = None
    status["ngrok_remote_tools"] = bool(
        (request.app.state.settings.ngrok_api_key or "").strip()
    )
    return status


@router.get("/api/tunnel/ngrok-agent-sessions", include_in_schema=False)
async def ngrok_agent_sessions(request: Request):
    """List online ngrok agent sessions (requires ``DATALOADER_NGROK_API_KEY``)."""
    key = (request.app.state.settings.ngrok_api_key or "").strip()
    if not key:
        return {
            "enabled": False,
            "message": "Set DATALOADER_NGROK_API_KEY to enable listing remote agents.",
            "sessions": [],
        }
    try:
        data = await ngrok_cloud.list_tunnel_sessions(api_key=key)
        sessions = data.get("tunnel_sessions") or []
        slim = []
        for s in sessions:
            if not isinstance(s, dict):
                continue
            slim.append(
                {
                    "id": s.get("id", ""),
                    "started_at": s.get("started_at", ""),
                    "region": s.get("region", ""),
                    "os": s.get("os", ""),
                    "ip": s.get("ip", ""),
                    "agent_version": s.get("agent_version", ""),
                }
            )
        return {"enabled": True, "sessions": slim, "error": None}
    except httpx.HTTPStatusError as exc:
        body = ""
        try:
            body = exc.response.text or ""
        except Exception:
            pass
        logger.warning("ngrok API list sessions failed: {} {}", exc.response.status_code, body)
        return JSONResponse(
            {
                "enabled": True,
                "sessions": [],
                "error": f"HTTP {exc.response.status_code}: {body[:200]}",
            },
            status_code=200,
        )
    except Exception as exc:
        logger.warning("ngrok API list sessions failed: {}", exc)
        return JSONResponse(
            {"enabled": True, "sessions": [], "error": str(exc)},
            status_code=200,
        )


@router.post("/api/tunnel/ngrok-agent-sessions/stop", include_in_schema=False)
async def ngrok_agent_session_stop(
    request: Request,
    session_id: str = Form(...),
):
    """Stop a remote ngrok agent session (frees a slot for ERR_NGROK_108)."""
    key = (request.app.state.settings.ngrok_api_key or "").strip()
    if not key:
        return JSONResponse(
            {"ok": False, "error": "DATALOADER_NGROK_API_KEY is not set."},
            status_code=400,
        )
    sid = session_id.strip()
    if not sid:
        return JSONResponse({"ok": False, "error": "session_id required"}, status_code=400)
    try:
        await ngrok_cloud.stop_tunnel_session(api_key=key, session_id=sid)
        return {"ok": True}
    except httpx.HTTPStatusError as exc:
        body = exc.response.text or ""
        return JSONResponse(
            {"ok": False, "error": f"HTTP {exc.response.status_code}: {body[:300]}"},
            status_code=502,
        )
    except Exception as exc:
        logger.warning("ngrok stop session failed: {}", exc)
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)


@router.post("/api/tunnel/check-org-webhook", include_in_schema=False)
async def check_org_webhook(
    request: Request,
    api_key: str = Form(""),
    org_id: str = Form(""),
):
    """Check whether the given MT org has a webhook endpoint for this tunnel URL.

    Used by the listener UI so users know if the org currently selected in the
    browser (sessionStorage) will receive events at this listener.
    Credentials are not stored; they should match what was entered on Setup.
    """
    api_key = api_key.strip()
    org_id = org_id.strip()
    mgr = request.app.state.tunnel
    tun = mgr.get_status()
    base_url = os.environ.get("MODERN_TREASURY_BASE_URL")
    base: dict = {
        "tunnel_connected": bool(tun.get("connected") and tun.get("url")),
        "tunnel_url": tun.get("url"),
        "expected_listener_url": None,
        "org_check": None,
        "org_id_preview": f"{org_id[:8]}…{org_id[-4:]}" if len(org_id) > 12 else org_id,
    }

    if not tun.get("connected") or not tun.get("url"):
        base["org_check"] = "no_tunnel"
        base["message"] = "Start the tunnel to verify MT webhook configuration."
        return base

    full_url = normalize_webhook_url(tun["url"]) + WEBHOOK_PATH
    base["expected_listener_url"] = full_url

    if not api_key or not org_id:
        base["org_check"] = "no_credentials"
        base["message"] = (
            "Enter API key and org ID on Setup (stored in this browser only). "
            "The ngrok tunnel already runs for this app — it does not depend on org."
        )
        return base

    try:
        endpoints = await list_webhook_endpoints(
            api_key=api_key,
            organization_id=org_id,
            base_url=base_url,
        )
        analysis = analyze_org_webhook_listeners(endpoints, full_url, WEBHOOK_PATH)
    except Exception as exc:
        logger.warning("Org webhook check failed: {}", exc)
        err = str(exc)
        if hasattr(exc, "response") and exc.response is not None:
            try:
                err = exc.response.text or err
            except Exception:
                pass
        base["org_check"] = "error"
        base["message"] = err
        return base

    if analysis["match"]:
        base["org_check"] = "match"
        base["endpoint_id"] = analysis["endpoint_id"]
        base["message"] = (
            f"This org ({base['org_id_preview']}) has a webhook endpoint "
            f"pointing at this listener URL."
        )
        return base

    if analysis.get("stale_url"):
        base["org_check"] = "stale_url"
        base["stale_url"] = analysis["stale_url"]
        su = analysis["stale_url"] or ""
        su_short = su if len(su) <= 64 else su[:61] + "..."
        base["message"] = (
            f"This org has a different webhook URL ({su_short}). "
            "MT will not send events to the current listener until you update it. "
            "Use Register in MT on this page or fix the endpoint in the MT dashboard."
        )
        return base

    base["org_check"] = "no_match"
    base["message"] = (
        f"No webhook endpoint in this org ({base['org_id_preview']}) uses this listener URL. "
        "MT will not deliver webhooks here for that org until you add one."
    )
    return base


@router.post("/api/tunnel/register-webhook", include_in_schema=False)
async def register_webhook(
    request: Request,
    api_key: str = Form(...),
    org_id: str = Form(...),
):
    """Create or update a webhook endpoint in MT pointing to the tunnel URL.

    Finds existing endpoints whose URL contains ``/webhooks/mt`` and
    updates them if the tunnel URL has changed; otherwise creates a new
    endpoint.  Captures the ``webhook_key`` (signing secret) on creation
    and auto-applies it for signature verification.
    """
    mgr = request.app.state.tunnel
    status = mgr.get_status()
    if not status.get("connected") or not status.get("url"):
        return JSONResponse(
            {"ok": False, "error": "No active tunnel. Start the tunnel first."},
            status_code=400,
        )

    tunnel_url = status["url"]
    full_webhook_url = normalize_webhook_url(tunnel_url) + WEBHOOK_PATH
    base_url = os.environ.get("MODERN_TREASURY_BASE_URL")

    try:
        endpoints = await list_webhook_endpoints(
            api_key=api_key,
            organization_id=org_id,
            base_url=base_url,
        )
        existing = None
        for ep in endpoints:
            u = ep.get("url") or ""
            if WEBHOOK_PATH in u:
                existing = ep
                break

        if existing:
            eid = existing.get("id", "")
            eurl = normalize_webhook_url(existing.get("url", ""))
            if eurl == full_webhook_url:
                return {
                    "ok": True,
                    "action": "already_registered",
                    "endpoint_id": eid,
                    "url": eurl,
                }
            await patch_webhook_endpoint(
                api_key=api_key,
                organization_id=org_id,
                endpoint_id=eid,
                url=full_webhook_url,
                base_url=base_url,
            )
            logger.info(
                "Updated MT webhook endpoint {} -> {}",
                eid,
                full_webhook_url,
            )
            return {
                "ok": True,
                "action": "updated",
                "endpoint_id": eid,
                "url": full_webhook_url,
            }

        created = await create_webhook_endpoint(
            api_key=api_key,
            organization_id=org_id,
            url=full_webhook_url,
            base_url=base_url,
        )
        cid = created.get("id", "")
        webhook_key = str(created.get("webhook_key") or "")
        mgr.save_webhook_endpoint(cid, webhook_key)

        if webhook_key:
            request.app.state.settings.webhook_secret = webhook_key
            logger.info(
                "Created MT webhook endpoint {}; signing secret auto-configured",
                cid,
            )
        else:
            logger.info("Created MT webhook endpoint {}", cid)

        return {
            "ok": True,
            "action": "created",
            "endpoint_id": cid,
            "url": full_webhook_url,
            "webhook_key_captured": bool(webhook_key),
        }

    except Exception as exc:
        logger.warning("Webhook registration failed: {}", exc)
        err_msg = str(exc)
        if hasattr(exc, "response") and exc.response is not None:
            try:
                err_msg = exc.response.text or err_msg
            except Exception:
                pass
        status = 500
        if hasattr(exc, "response") and exc.response is not None:
            status = getattr(exc.response, "status_code", None) or 500
            if status < 400:
                status = 500
        return JSONResponse({"ok": False, "error": err_msg}, status_code=status)
