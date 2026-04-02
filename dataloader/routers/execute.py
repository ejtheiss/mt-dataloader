"""Execute routes: execute page and SSE stream."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Request
from loguru import logger
from modern_treasury import AsyncModernTreasury
from sse_starlette import EventSourceResponse, ServerSentEvent

from dataloader.engine import execute, generate_run_id
from dataloader.engine.run_meta import config_hash, resolve_manifest_path
from dataloader.handlers import build_handler_dispatch, build_update_dispatch
from dataloader.routers.deps import SessionFormDep, SettingsDep, TemplatesDep
from dataloader.session import sessions
from dataloader.webhooks import index_resource, register_run_org
from db.repositories import correlation as correlation_repo
from db.repositories import runs as runs_repo
from helpers import error_html, error_response
from models import DisplayPhase, RunManifest
from sse_helpers import make_emit_sse, sse_error_response

router = APIRouter(tags=["execute"])


async def _persist_manifest_status_to_db(
    session_factory,
    runs_dir: str,
    run_id: str,
) -> None:
    """Mirror terminal manifest status and list counts into ``runs`` (best-effort)."""
    path = resolve_manifest_path(Path(runs_dir), run_id)
    if path is None:
        return
    try:
        manifest = RunManifest.load(path)
    except Exception as exc:
        logger.bind(run_id=run_id).warning("db finalize_run: could not load manifest: {}", exc)
        return
    try:
        async with session_factory() as s:
            await runs_repo.finalize_run(
                s,
                run_id=manifest.run_id,
                status=manifest.status,
                completed_at=manifest.completed_at,
                resources_created_count=len(manifest.resources_created),
                resources_staged_count=len(manifest.resources_staged)
                if manifest.resources_staged
                else 0,
                resources_failed_count=len(manifest.resources_failed)
                if manifest.resources_failed
                else 0,
                manifest_json=manifest.model_dump_json(),
            )
            await s.commit()
    except Exception as exc:
        logger.bind(run_id=run_id).warning("db finalize_run failed (non-fatal): {}", exc)


@router.post("/api/execute")
async def execute_page(
    request: Request,
    templates: TemplatesDep,
    sess: SessionFormDep,
):
    """Return execute page with pre-rendered rows and SSE container."""
    if not sess:
        return error_response("Session Expired", "Please re-validate your config.")

    return templates.TemplateResponse(
        request,
        "execute.html",
        {
            "session_token": sess.session_token,
            "preview_items": sess.preview_items,
            "batches": sess.batches,
            "resource_count": sum(len(b) for b in sess.batches),
            "display_phases": DisplayPhase,
        },
    )


@router.get("/api/execute/stream")
async def execute_stream(
    request: Request,
    templates: TemplatesDep,
    settings: SettingsDep,
    session_token: str,
):
    """SSE stream endpoint. Pops session and runs the DAG engine."""
    session = sessions.pop(session_token, None)
    if not session:
        return sse_error_response(
            error_html=error_html,
            title="Session Expired",
            detail="Please re-validate your config.",
        )

    async def event_generator():
        queue: asyncio.Queue[ServerSentEvent | None] = asyncio.Queue()
        emit_sse = make_emit_sse(templates, queue)
        run_id = generate_run_id()
        semaphore = asyncio.Semaphore(settings.max_concurrent_requests)

        config_path = Path(settings.runs_dir) / f"{run_id}_config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(session.config_json_text, encoding="utf-8")

        disconnected = False
        session_factory = getattr(request.app.state, "async_session_factory", None)
        default_uid = getattr(request.app.state, "default_user_id", None)

        async def run_engine():
            nonlocal disconnected
            started_at = datetime.now(timezone.utc).isoformat()
            cfg_h = config_hash(session.config)
            if session_factory is not None and default_uid is not None:
                try:
                    async with session_factory() as s:
                        await runs_repo.ensure_run(
                            s,
                            run_id=run_id,
                            user_id=default_uid,
                            mt_org_id=session.org_id or None,
                            mt_org_label=session.org_label or None,
                            config_hash=cfg_h,
                            started_at=started_at,
                        )
                        await s.commit()
                except Exception as exc:
                    logger.bind(run_id=run_id).warning("db ensure_run failed (non-fatal): {}", exc)

            async def on_resource_created_db(rid: str, created_id: str, tref: str) -> None:
                index_resource(rid, created_id, tref)
                if session_factory is None:
                    return
                try:
                    async with session_factory() as s:
                        await correlation_repo.upsert_correlation(
                            s,
                            created_id=created_id,
                            run_id=rid,
                            typed_ref=tref,
                        )
                        await s.commit()
                except Exception as exc:
                    logger.bind(run_id=rid, created_id=created_id).warning(
                        "db correlation upsert failed (non-fatal): {}", exc
                    )

            async def on_run_org_registered_db(rid: str, org_id: str) -> None:
                register_run_org(rid, org_id)
                if session_factory is None:
                    return
                try:
                    async with session_factory() as s:
                        await runs_repo.update_mt_org(s, rid, org_id)
                        await s.commit()
                except Exception as exc:
                    logger.warning("db update_mt_org failed (non-fatal): {}", exc)

            async with AsyncModernTreasury(
                api_key=session.api_key,
                organization_id=session.org_id,
            ) as client:
                handler_dispatch = build_handler_dispatch(client, emit_sse)
                update_dispatch = build_update_dispatch(client, emit_sse)
                try:
                    manifest = await execute(
                        config=session.config,
                        registry=session.registry,
                        handler_dispatch=handler_dispatch,
                        run_id=run_id,
                        semaphore=semaphore,
                        emit_sse=emit_sse,
                        is_disconnected=lambda: disconnected,
                        runs_dir=settings.runs_dir,
                        on_resource_created=on_resource_created_db,
                        skip_refs=session.skip_refs or None,
                        update_refs=session.update_refs or None,
                        update_dispatch=update_dispatch,
                        mt_org_id=session.org_id,
                        mt_org_label=session.org_label,
                        on_run_org_registered=on_run_org_registered_db,
                    )
                    html = templates.get_template("partials/run_complete.html").render(
                        manifest=manifest, run_id=run_id
                    )
                    await queue.put(ServerSentEvent(data=html, event="run_complete"))
                except Exception as exc:
                    logger.bind(run_id=run_id, error=str(exc)).error("Execution failed")
                    html = templates.get_template("partials/error.html").render(error=str(exc))
                    await queue.put(ServerSentEvent(data=html, event="error"))
                finally:
                    if session_factory is not None:
                        await _persist_manifest_status_to_db(
                            session_factory, settings.runs_dir, run_id
                        )
                    await queue.put(ServerSentEvent(data="", event="close"))
                    await queue.put(None)

        task = asyncio.create_task(run_engine())

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        except asyncio.CancelledError:
            disconnected = True
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    return EventSourceResponse(event_generator(), ping=15)
