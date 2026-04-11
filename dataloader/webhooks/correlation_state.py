"""Process-wide webhook correlation: MT resource id → (run_id, typed_ref) and run → org.

HTTP routes read/update this module; startup hydrates from ``run_created_resources``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from loguru import logger

from dataloader.webhooks.correlate import correlate_webhook_data
from models.run_views import CreatedResourceRow

resource_correlation_index: dict[str, tuple[str, str]] = {}
run_org_by_run_id: dict[str, str] = {}


def index_resource(run_id: str, created_id: str, typed_ref: str) -> None:
    """Register a created resource (see ``engine.execute`` ``on_resource_created``)."""
    resource_correlation_index[created_id] = (run_id, typed_ref)


def register_run_org(run_id: str, org_id: str) -> None:
    """Record MT org for a run (inbound webhooks can label rows before DB reread)."""
    if run_id and org_id:
        run_org_by_run_id[run_id] = org_id


def mt_org_for_run(run_id: str | None) -> str | None:
    """MT org id for a correlated run, if known."""
    if not run_id:
        return None
    return run_org_by_run_id.get(run_id)


def ensure_run_indexed_from_rows(run_id: str, resources: Sequence[CreatedResourceRow]) -> None:
    """Populate the correlation index from persisted created rows (run detail, cold start)."""
    for entry in resources:
        if entry.created_id and entry.created_id != "SKIPPED":
            resource_correlation_index[entry.created_id] = (run_id, entry.typed_ref)
        for child_key, child_id in entry.child_refs.items():
            if child_id and child_id not in resource_correlation_index:
                resource_correlation_index[child_id] = (run_id, f"{entry.typed_ref}.{child_key}")


def replace_runtime_correlation_state(
    correlations: Sequence[tuple[str, str, str]],
    run_org_pairs: Sequence[tuple[str, str]],
) -> None:
    """Replace maps from DB-derived correlation tuples + ``runs`` org rows."""
    resource_correlation_index.clear()
    run_org_by_run_id.clear()
    for created_id, rid, typed_ref in correlations:
        resource_correlation_index[created_id] = (rid, typed_ref)
    for rid, org_id in run_org_pairs:
        if org_id:
            run_org_by_run_id[rid] = org_id


def correlation_index_size() -> int:
    return len(resource_correlation_index)


def correlate_inbound_payload(data: Any) -> tuple[str | None, str | None]:
    """Resolve webhook ``data`` like POST ``/webhooks/mt`` (jobs, tests, recorrelate)."""
    return correlate_webhook_data(data, resource_correlation_index)


def rebuild_correlation_index(_runs_dir: str) -> int:
    """Deprecated: disk manifest scan removed — correlation loads from DB at startup only."""
    logger.warning(
        "rebuild_correlation_index is a no-op; use bootstrap_webhook_correlation / DB hydration"
    )
    return 0
