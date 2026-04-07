"""Process-wide webhook correlation: MT resource id → (run_id, typed_ref) and run → org.

HTTP routes read/update this module; startup backfill replaces it from the DB.
Pure logic for matching payloads lives in :mod:`dataloader.webhooks.correlate`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from loguru import logger

from dataloader.engine.run_meta import list_manifest_ids
from dataloader.webhooks.correlate import correlate_webhook_data
from models import RunManifest

resource_correlation_index: dict[str, tuple[str, str]] = {}
run_org_by_run_id: dict[str, str] = {}


def index_resource(run_id: str, created_id: str, typed_ref: str) -> None:
    """Register a created resource (see ``engine.execute`` ``on_resource_created``)."""
    resource_correlation_index[created_id] = (run_id, typed_ref)


def register_run_org(run_id: str, org_id: str) -> None:
    """Record MT org for a run (inbound webhooks can label rows before manifest reread)."""
    if run_id and org_id:
        run_org_by_run_id[run_id] = org_id


def mt_org_for_run(run_id: str | None) -> str | None:
    """MT org id for a correlated run, if known."""
    if not run_id:
        return None
    return run_org_by_run_id.get(run_id)


def ensure_run_indexed(run_id: str, manifest: Any) -> None:
    """Populate the correlation index from a manifest (e.g. run detail after restart)."""
    for entry in manifest.resources_created:
        if entry.created_id not in resource_correlation_index:
            resource_correlation_index[entry.created_id] = (run_id, entry.typed_ref)
        for child_key, child_id in entry.child_refs.items():
            if child_id not in resource_correlation_index:
                resource_correlation_index[child_id] = (
                    run_id,
                    f"{entry.typed_ref}.{child_key}",
                )


def replace_runtime_correlation_state(
    correlations: Sequence[tuple[str, str, str]],
    run_org_pairs: Sequence[tuple[str, str]],
) -> None:
    """Replace maps from ``resource_correlation`` + ``runs`` rows (startup)."""
    resource_correlation_index.clear()
    run_org_by_run_id.clear()
    for created_id, run_id, typed_ref in correlations:
        resource_correlation_index[created_id] = (run_id, typed_ref)
    for rid, org_id in run_org_pairs:
        if org_id:
            run_org_by_run_id[rid] = org_id


def correlation_index_size() -> int:
    return len(resource_correlation_index)


def correlate_inbound_payload(data: Any) -> tuple[str | None, str | None]:
    """Resolve webhook ``data`` like POST ``/webhooks/mt`` (jobs, tests, recorrelate)."""
    return correlate_webhook_data(data, resource_correlation_index)


def rebuild_correlation_index(runs_dir: str) -> int:
    """Disk repair: load ``runs/*.json`` into the in-memory index (no DB bootstrap)."""
    count = 0
    runs_path = Path(runs_dir)
    for run_id in list_manifest_ids(runs_dir):
        try:
            manifest = RunManifest.load(runs_path / f"{run_id}.json")
        except Exception as exc:
            logger.warning("Skipping manifest {} during index rebuild: {}", run_id, exc)
            continue
        oid = getattr(manifest, "mt_org_id", None)
        if oid:
            run_org_by_run_id[run_id] = oid
        for entry in manifest.resources_created:
            resource_correlation_index[entry.created_id] = (run_id, entry.typed_ref)
            count += 1
            for child_key, child_id in entry.child_refs.items():
                resource_correlation_index[child_id] = (
                    run_id,
                    f"{entry.typed_ref}.{child_key}",
                )
                count += 1

    logger.info(
        "Correlation index rebuilt: {} IDs from {} runs",
        count,
        len(list_manifest_ids(runs_dir)),
    )
    return count
