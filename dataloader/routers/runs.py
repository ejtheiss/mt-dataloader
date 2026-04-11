"""Runs listing routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger

from dataloader.routers.deps import CurrentAppUserDep, SettingsDep, TemplatesDep
from dataloader.run_access import user_to_ctx
from dataloader.runs_pagination import (
    RunsSeekCursorError,
    decode_runs_seek_cursor,
    encode_runs_seek_cursor,
)
from dataloader.view_models import runs_list_fragment_context
from db.repositories import run_artifacts
from db.repositories import runs as runs_repo
from models import RunListJsonResponse

router = APIRouter(tags=["runs"])
HTML_RUNS_HARD_CAP = 500


def _request_prefers_json(request: Request) -> bool:
    """Return True when client explicitly asks for JSON on ``/api/runs``."""
    accept = (request.headers.get("accept") or "").lower()
    return "application/json" in accept


async def _list_runs_json_payload(
    request: Request,
    current_user: CurrentAppUserDep,
    *,
    limit: int,
    offset: int,
    cursor: str | None,
    sort: str | None,
    dir: str,
    status: str | None,
    mt_org_id: str | None,
) -> RunListJsonResponse:
    """Build the JSON payload shared by ``/api/runs`` and ``/api/runs.json``."""
    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=503,
            detail="Runs JSON API requires the application database.",
        )

    sort_key = (sort or "").strip() or None
    if sort_key and sort_key not in runs_repo.RUN_LIST_COLUMN_SORT_KEYS:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid sort={sort_key!r}; use one of {sorted(runs_repo.RUN_LIST_COLUMN_SORT_KEYS)}.",
        )

    if cursor and cursor.strip() and offset != 0:
        raise HTTPException(
            status_code=400,
            detail="Do not pass offset when using cursor pagination.",
        )
    if cursor and cursor.strip() and sort_key:
        raise HTTPException(
            status_code=400,
            detail="cursor pagination requires default sort (omit sort=).",
        )

    cursor_after: tuple[str, str] | None = None
    if cursor and cursor.strip():
        try:
            cursor_after = decode_runs_seek_cursor(cursor)
        except RunsSeekCursorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    ctx = user_to_ctx(current_user)
    try:
        async with factory() as session:
            q = await runs_repo.query_run_rows_for_api(
                session,
                ctx,
                status=status,
                mt_org_id=mt_org_id,
                sort=sort_key,
                sort_dir=dir,
                limit=limit,
                offset=0 if cursor_after is not None else offset,
                fetch_extra_for_has_more=True,
                cursor_after=cursor_after,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("db query_run_rows_for_api (json) failed: {}", exc)
        raise HTTPException(status_code=503, detail="Database error") from exc

    next_cursor: str | None = None
    if q.has_more and q.rows:
        last = q.rows[-1]
        next_cursor = encode_runs_seek_cursor(last.started_at, last.run_id)

    resp_offset = 0 if cursor_after is not None else offset
    return RunListJsonResponse(
        items=q.rows,
        limit=limit,
        offset=resp_offset,
        has_more=q.has_more,
        next_cursor=next_cursor,
    )


@router.get("/runs", include_in_schema=False)
async def runs_page(request: Request, templates: TemplatesDep):
    return templates.TemplateResponse(request, "runs_page.html", {"title": "Runs"})


@router.get("/api/runs")
async def list_runs(
    request: Request,
    templates: TemplatesDep,
    settings: SettingsDep,
    current_user: CurrentAppUserDep,
    limit: int = Query(
        20,
        ge=1,
        le=100,
        description="JSON mode: page size (default 20, max 100). Ignored for HTML.",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="JSON mode rows to skip. Ignored for HTML unless JSON is requested.",
    ),
    cursor: str | None = Query(
        None,
        description="JSON mode keyset cursor (default sort only). Ignored for HTML.",
    ),
    sort: str | None = None,
    dir: str = "asc",
    status: str | None = None,
    mt_org_id: str | None = None,
):
    """List runs: Wave B uses SQLite when DB is up; ``user`` sees only owned rows."""
    if _request_prefers_json(request):
        return await _list_runs_json_payload(
            request,
            current_user,
            limit=limit,
            offset=offset,
            cursor=cursor,
            sort=sort,
            dir=dir,
            status=status,
            mt_org_id=mt_org_id,
        )

    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=503,
            detail="Runs list requires the application database.",
        )
    ctx = user_to_ctx(current_user)
    try:
        async with factory() as session:
            q = await runs_repo.query_run_rows_for_api(
                session,
                ctx,
                status=status,
                mt_org_id=mt_org_id,
                sort=sort,
                sort_dir=dir,
                limit=HTML_RUNS_HARD_CAP,
                offset=0,
            )
            rows = q.rows
    except Exception as exc:
        logger.warning("db query_run_rows_for_api failed: {}", exc)
        raise HTTPException(status_code=503, detail="Database error") from exc

    return templates.TemplateResponse(
        request,
        "runs_page.html",
        runs_list_fragment_context(rows=rows, sort=sort, dir=dir, status=status),
        block_name="runs_list",
    )


@router.get(
    "/api/runs.json",
    response_model=RunListJsonResponse,
    summary="List runs (JSON)",
    operation_id="list_runs_json",
    tags=["runs", "agent"],
    description=(
        "Paginated run metadata for scripts and integrations. Requires a working app database; "
        "does not fall back to scanning manifest files on disk. "
        "Use **offset** for arbitrary pages (rows may shift if data changes while paging), "
        "or **cursor** with default sort only for stable keyset paging (newest ``started_at`` first)."
    ),
)
async def list_runs_json(
    request: Request,
    current_user: CurrentAppUserDep,
    limit: int = Query(
        20,
        ge=1,
        le=100,
        description="Page size (default 20, max 100).",
    ),
    offset: int = Query(0, ge=0, description="Rows to skip (omit when using ``cursor``)."),
    cursor: str | None = Query(
        None,
        description="Keyset continuation from ``next_cursor`` (default sort only; do not combine "
        "with ``offset``>0 or column ``sort``).",
    ),
    sort: str | None = Query(
        None,
        description="Optional column: run_id, status, resources, staged, failed. "
        "Omitted = newest started_at first.",
    ),
    dir: str = Query(
        "asc",
        description="Sort direction when ``sort`` is set (asc or desc). Ignored for default ordering.",
    ),
    status: str | None = Query(None, description="Filter by run status."),
    mt_org_id: str | None = Query(None, description="Filter by Modern Treasury organization id."),
):
    """JSON run list with SQL-backed filters and pagination (Plan 09)."""
    return await _list_runs_json_payload(
        request,
        current_user,
        limit=limit,
        offset=offset,
        cursor=cursor,
        sort=sort,
        dir=dir,
        status=status,
        mt_org_id=mt_org_id,
    )


@router.get("/api/runs/{run_id}/drawer")
async def run_drawer(
    request: Request,
    run_id: str,
    templates: TemplatesDep,
    current_user: CurrentAppUserDep,
):
    """Return drawer partial for a single run (DB row — same ``status`` as the runs table)."""
    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        async with factory() as session:
            row = await runs_repo.fetch_run_drawer_row(session, run_id, user_to_ctx(current_user))
    except Exception as exc:
        logger.warning("run drawer DB lookup failed: {}", exc)
        raise HTTPException(status_code=503, detail="Database error") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return templates.TemplateResponse(request, "partials/run_drawer.html", {"run": row})


@router.get("/api/runs/{run_id}/resources/drawer")
async def resource_drawer_in_run(
    request: Request,
    run_id: str,
    templates: TemplatesDep,
    settings: SettingsDep,
    current_user: CurrentAppUserDep,
    ref: str = "",
):
    """Return drawer partial for a single resource within a run."""
    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is None:
        raise HTTPException(status_code=503, detail="Database unavailable")
    try:
        async with factory() as session:
            item = await run_artifacts.fetch_created_resource_row(
                session, run_id, ref, user_to_ctx(current_user)
            )
    except Exception as exc:
        logger.warning("resource drawer DB lookup failed: {}", exc)
        raise HTTPException(status_code=503, detail="Database error") from exc
    if item is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return templates.TemplateResponse(
        request,
        "partials/resource_drawer.html",
        {"item": item},
    )
