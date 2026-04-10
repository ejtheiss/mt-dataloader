"""GET ``/listen`` — standalone webhook listener with tunnel auto-detection."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Request
from loguru import logger

from dataloader.routers.deps import CurrentAppUserDep, SettingsDep, TemplatesDep, TunnelDep
from dataloader.run_access import run_is_readable, user_to_ctx
from dataloader.tunnel import TunnelManager, first_https_tunnel_url
from dataloader.webhooks.webhook_persist import (
    _load_webhook_history_for_run,
    enrich_webhooks_run_org,
)
from db.repositories import runs as runs_repo
from db.repositories import webhooks as webhooks_repo

router = APIRouter()


def _detect_tunnel_from_manager(mgr: TunnelManager | None) -> str | None:
    """Check TunnelManager (pyngrok-managed) for an active tunnel URL."""
    if mgr is None:
        return None
    status = mgr.get_status()
    if status.get("connected") and status.get("url"):
        return status["url"]
    return None


async def _detect_tunnel_legacy() -> str | None:
    """Probe ngrok local API for a public tunnel URL (external ngrok)."""
    try:
        async with httpx.AsyncClient(timeout=2.0) as http:
            resp = await http.get("http://127.0.0.1:4040/api/tunnels")
            if resp.status_code == 200:
                data = resp.json()
                url = first_https_tunnel_url(data)
                if url:
                    return url
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        pass
    return None


async def _detect_tunnel(mgr: TunnelManager | None) -> str | None:
    """Try TunnelManager first, fall back to probing external ngrok."""
    url = _detect_tunnel_from_manager(mgr)
    if url:
        return url
    return await _detect_tunnel_legacy()


@router.get("/listen", include_in_schema=False)
async def listen_page(
    request: Request,
    settings: SettingsDep,
    templates: TemplatesDep,
    tunnel_mgr: TunnelDep,
    current_user: CurrentAppUserDep,
    run_id: str | None = None,
):
    """Standalone webhook listener with tunnel auto-detection and run filter."""
    tunnel_url = await _detect_tunnel(tunnel_mgr)

    mgr = tunnel_mgr
    saved_authtoken = ""
    saved_domain = ""
    saved_webhook_endpoint_id = ""
    if mgr:
        saved_authtoken = settings.ngrok_authtoken or mgr.saved_authtoken
        saved_domain = settings.ngrok_domain or mgr.saved_domain
        saved_webhook_endpoint_id = mgr.saved_webhook_endpoint_id

    tunnel_setup_collapsed = bool(tunnel_url and saved_webhook_endpoint_id)

    webhook_history: list[dict] = []
    run_ids: list[str] = []
    run_org_map: dict[str, str] = {}
    listen_run_list_ok = False
    ctx = user_to_ctx(current_user)
    requested = (run_id or "").strip() or None
    stream_run_id: str | None = None

    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is not None:
        try:
            async with factory() as session:
                run_ids = await webhooks_repo.list_run_ids_with_webhooks_ordered(session, ctx)
                run_org_map = await runs_repo.map_mt_org_ids_for_run_ids(session, run_ids)

                if requested and await run_is_readable(request, settings, requested, current_user):
                    stream_run_id = requested
                    webhook_history = await _load_webhook_history_for_run(
                        request, settings, requested, current_user
                    )
                elif not requested:
                    webhook_history = await webhooks_repo.list_recent_webhook_history_for_listener(
                        session, ctx, limit=400
                    )
                    hist_runs = {w["run_id"] for w in webhook_history if w.get("run_id")}
                    extra_map = await runs_repo.map_mt_org_ids_for_run_ids(
                        session, list(hist_runs - set(run_org_map.keys()))
                    )
                    run_org_map.update(extra_map)

            enrich_webhooks_run_org(webhook_history, run_org_map)
            listen_run_list_ok = True
        except Exception as exc:
            logger.warning("listen page: DB run list failed: {}", exc)

    show_run_filter_chip = bool(stream_run_id and stream_run_id in run_ids)

    return templates.TemplateResponse(
        request,
        "listen.html",
        {
            "tunnel_url": tunnel_url,
            "webhook_path": "/webhooks/mt",
            "webhook_history": webhook_history,
            "run_ids": run_ids,
            "run_org_map": run_org_map,
            "selected_run_id": stream_run_id,
            "show_run_filter_chip": show_run_filter_chip,
            "saved_authtoken": saved_authtoken,
            "saved_domain": saved_domain,
            "saved_webhook_endpoint_id": saved_webhook_endpoint_id,
            "tunnel_setup_collapsed": tunnel_setup_collapsed,
            "listen_run_list_ok": listen_run_list_ok,
        },
    )
