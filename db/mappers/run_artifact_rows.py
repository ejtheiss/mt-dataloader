"""Map ``run_*`` ORM rows to immutable view DTOs (``models.run_views``)."""

from __future__ import annotations

import json
from typing import Any

from db.tables import RunCreatedResource, RunResourceFailure, RunStagedItem
from models.run_views import CreatedResourceRow, FailedResourceRow, StagedItemView


def child_refs_from_json_column(raw: str | None) -> dict[str, str]:
    if not raw or raw == "{}":
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def orm_created_to_row(row: RunCreatedResource) -> CreatedResourceRow:
    return CreatedResourceRow(
        batch=row.batch,
        resource_type=row.resource_type,
        typed_ref=row.typed_ref,
        created_id=row.created_id,
        created_at=row.created_at,
        deletable=bool(row.deletable),
        child_refs=child_refs_from_json_column(row.child_refs_json),
        cleanup_status=row.cleanup_status,
    )


def orm_failure_to_row(row: RunResourceFailure) -> FailedResourceRow:
    return FailedResourceRow(
        typed_ref=row.typed_ref,
        error=row.error,
        failed_at=row.failed_at,
        error_type=row.error_type,
        http_status=row.http_status,
        error_cause=row.error_cause,
    )


def orm_staged_to_view(row: RunStagedItem) -> StagedItemView:
    return StagedItemView(
        resource_type=row.resource_type,
        typed_ref=row.typed_ref,
        staged_at=row.staged_at,
    )
