"""Normalized run artifacts + webhook correlation rows (DB-only)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from db.mappers.run_artifact_rows import (
    child_refs_from_json_column,
    orm_created_to_row,
    orm_failure_to_row,
    orm_staged_to_view,
)
from db.repositories.runs import RunAccessContext, get_run_row_for_access
from db.tables import Run, RunCreatedResource, RunResourceFailure, RunStagedItem
from models.run_views import CreatedResourceRow, RunDetailView


async def fetch_correlation_index_rows(session: AsyncSession) -> list[tuple[str, str, str]]:
    """All webhook index tuples ``(created_id, run_id, typed_ref)`` including child IDs."""
    result = await session.execute(
        select(
            RunCreatedResource.created_id,
            RunCreatedResource.run_id,
            RunCreatedResource.typed_ref,
            RunCreatedResource.child_refs_json,
        )
    )
    out: list[tuple[str, str, str]] = []
    for cid, rid, tref, cref in result.all():
        out.append((cid, rid, tref))
        for ck, child_id in child_refs_from_json_column(cref).items():
            if child_id:
                out.append((child_id, rid, f"{tref}.{ck}"))
    return out


async def fetch_run_detail_view(
    session: AsyncSession,
    run_id: str,
    ctx: RunAccessContext,
) -> RunDetailView | None:
    base = await get_run_row_for_access(session, run_id, ctx)
    if base is None:
        return None

    created = await session.scalars(
        select(RunCreatedResource)
        .where(RunCreatedResource.run_id == run_id)
        .order_by(RunCreatedResource.batch, RunCreatedResource.typed_ref)
    )
    failures = await session.scalars(
        select(RunResourceFailure)
        .where(RunResourceFailure.run_id == run_id)
        .order_by(RunResourceFailure.id)
    )
    staged_result = await session.scalars(
        select(RunStagedItem)
        .where(RunStagedItem.run_id == run_id)
        .order_by(RunStagedItem.staged_at)
    )
    staged_orm_list = staged_result.all()

    created_rows = tuple(orm_created_to_row(r) for r in created.all())
    failed_rows = tuple(orm_failure_to_row(r) for r in failures.all())
    staged_rows = tuple(orm_staged_to_view(r) for r in staged_orm_list)

    staged_payloads: dict[str, dict[str, Any]] = {}
    for r in staged_orm_list:
        try:
            payload = json.loads(r.payload_json)
            if isinstance(payload, dict):
                staged_payloads[r.typed_ref] = payload
        except (json.JSONDecodeError, TypeError):
            staged_payloads[r.typed_ref] = {}

    cfg = base.config_json or "{}"

    return RunDetailView(
        run_id=base.run_id,
        status=base.status,
        started_at=base.started_at,
        completed_at=base.completed_at,
        config_hash=base.config_hash,
        mt_org_id=base.mt_org_id,
        mt_org_label=base.mt_org_label,
        resources_created=created_rows,
        resources_failed=failed_rows,
        resources_staged=staged_rows,
        config_json=cfg,
        staged_payloads=staged_payloads,
    )


async def fetch_created_resource_row(
    session: AsyncSession,
    run_id: str,
    typed_ref: str,
    ctx: RunAccessContext,
) -> CreatedResourceRow | None:
    if await get_run_row_for_access(session, run_id, ctx) is None:
        return None
    row = await session.scalar(
        select(RunCreatedResource).where(
            RunCreatedResource.run_id == run_id,
            RunCreatedResource.typed_ref == typed_ref,
        )
    )
    return orm_created_to_row(row) if row else None


async def fetch_staged_payload_and_meta(
    session: AsyncSession,
    run_id: str,
    typed_ref: str,
    ctx: RunAccessContext,
) -> tuple[dict[str, Any], str] | None:
    if await get_run_row_for_access(session, run_id, ctx) is None:
        return None
    row = await session.scalar(
        select(RunStagedItem).where(
            RunStagedItem.run_id == run_id,
            RunStagedItem.typed_ref == typed_ref,
        )
    )
    if row is None:
        return None
    try:
        payload = json.loads(row.payload_json)
        if not isinstance(payload, dict):
            payload = {}
    except (json.JSONDecodeError, TypeError):
        payload = {}
    return payload, row.staged_at


async def fetch_cleanup_created_rows(
    session: AsyncSession,
    run_id: str,
    ctx: RunAccessContext,
) -> list[CreatedResourceRow]:
    """Creation order reversed (cleanup walks newest / reverse DAG order)."""
    if await get_run_row_for_access(session, run_id, ctx) is None:
        return []
    result = await session.scalars(
        select(RunCreatedResource)
        .where(RunCreatedResource.run_id == run_id)
        .order_by(RunCreatedResource.batch.desc(), RunCreatedResource.typed_ref.desc())
    )
    return [orm_created_to_row(r) for r in result.all()]


async def insert_created_resource_row(
    session: AsyncSession,
    *,
    run_id: str,
    batch: int,
    resource_type: str,
    typed_ref: str,
    created_id: str,
    created_at: str,
    deletable: bool,
    child_refs: dict[str, str],
    cleanup_status: str | None = None,
) -> None:
    stmt = (
        sqlite_insert(RunCreatedResource)
        .values(
            created_id=created_id,
            run_id=run_id,
            batch=batch,
            resource_type=resource_type,
            typed_ref=typed_ref,
            created_at=created_at,
            deletable=deletable,
            cleanup_status=cleanup_status,
            child_refs_json=json.dumps(child_refs),
        )
        .on_conflict_do_update(
            index_elements=[RunCreatedResource.created_id],
            set_={
                "run_id": run_id,
                "batch": batch,
                "resource_type": resource_type,
                "typed_ref": typed_ref,
                "created_at": created_at,
                "deletable": deletable,
                "cleanup_status": cleanup_status,
                "child_refs_json": json.dumps(child_refs),
            },
        )
    )
    await session.execute(stmt)


async def insert_failure_row(
    session: AsyncSession,
    *,
    run_id: str,
    typed_ref: str,
    error: str,
    failed_at: str,
    error_type: str | None,
    http_status: int | None,
    error_cause: str | None,
) -> None:
    session.add(
        RunResourceFailure(
            run_id=run_id,
            typed_ref=typed_ref,
            error=error,
            failed_at=failed_at,
            error_type=error_type,
            http_status=http_status,
            error_cause=error_cause,
        )
    )


async def upsert_staged_item(
    session: AsyncSession,
    *,
    run_id: str,
    typed_ref: str,
    resource_type: str,
    staged_at: str,
    payload_json: str,
) -> None:
    stmt = (
        sqlite_insert(RunStagedItem)
        .values(
            run_id=run_id,
            typed_ref=typed_ref,
            resource_type=resource_type,
            staged_at=staged_at,
            payload_json=payload_json,
        )
        .on_conflict_do_update(
            index_elements=[RunStagedItem.run_id, RunStagedItem.typed_ref],
            set_={
                "resource_type": resource_type,
                "staged_at": staged_at,
                "payload_json": payload_json,
            },
        )
    )
    await session.execute(stmt)


async def delete_staged_item(session: AsyncSession, run_id: str, typed_ref: str) -> None:
    await session.execute(
        delete(RunStagedItem).where(
            RunStagedItem.run_id == run_id,
            RunStagedItem.typed_ref == typed_ref,
        )
    )


async def set_run_config_json(session: AsyncSession, run_id: str, config_json: str) -> None:
    row = await session.get(Run, run_id)
    if row is not None:
        row.config_json = config_json


async def merge_run_extras_json(
    session: AsyncSession,
    run_id: str,
    extras: dict[str, Any],
) -> None:
    row = await session.get(Run, run_id)
    if row is None:
        return
    cur: dict[str, Any] = {}
    if row.run_extras_json:
        try:
            loaded = json.loads(row.run_extras_json)
            if isinstance(loaded, dict):
                cur = loaded
        except (json.JSONDecodeError, TypeError):
            cur = {}
    cur.update(extras)
    row.run_extras_json = json.dumps(cur) if cur else None
