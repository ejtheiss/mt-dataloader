"""Startup backfill: legacy disk run JSON → SQLite artifacts, then hydrate webhook correlation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dataloader.legacy_run_disk import (
    LegacyRunDiskSnapshot,
    list_legacy_run_json_ids,
    load_legacy_run_json_dict,
    resolve_legacy_run_json_path,
)
from dataloader.webhooks.correlation_state import (
    correlate_inbound_payload,
    correlation_index_size,
    replace_runtime_correlation_state,
)
from db.repositories import run_artifacts, runs as runs_repo
from db.repositories.run_artifacts import fetch_correlation_index_rows
from db.repositories import webhooks as webhooks_repo
from models.run_execution_entries import FailedEntry, ManifestEntry, StagedEntry


async def backfill_missing_runs_from_disk(
    session: AsyncSession,
    runs_dir: str | Path,
    *,
    default_user_id: int,
) -> dict[str, int]:
    """Upsert ``runs`` + normalized artifacts for legacy JSON with no ``runs`` row yet."""
    rdir = Path(runs_dir)
    stats = {"runs_backfilled": 0, "artifact_rows": 0}
    existing = await runs_repo.list_run_id_set(session)

    for run_id in list_legacy_run_json_ids(rdir):
        if run_id in existing:
            continue
        path = resolve_legacy_run_json_path(rdir, run_id)
        if path is None:
            logger.bind(run_id=run_id).warning("backfill: legacy run JSON path not found, skipping")
            continue
        try:
            raw = load_legacy_run_json_dict(path)
            snap = LegacyRunDiskSnapshot.model_validate(raw)
        except (OSError, TypeError, ValueError, ValidationError) as exc:
            logger.bind(run_id=run_id, path=str(path)).warning("backfill: load/validate failed: {}", exc)
            continue

        cfg_path = rdir / f"{run_id}_config.json"
        cfg_text: str | None = None
        if cfg_path.is_file():
            try:
                cfg_text = cfg_path.read_text(encoding="utf-8")
            except OSError:
                cfg_text = None

        extras: dict[str, Any] = {}
        for k in ("generation_recipe", "compile_id", "seed_version"):
            v = getattr(snap, k, None)
            if v is not None:
                extras[k] = v
        extras_json = json.dumps(extras) if extras else None

        await runs_repo.backfill_upsert_run(
            session,
            run_id=snap.run_id,
            user_id=default_user_id,
            mt_org_id=snap.mt_org_id,
            mt_org_label=snap.mt_org_label,
            status=str(snap.status),
            config_hash=snap.config_hash or None,
            started_at=snap.started_at or "1970-01-01T00:00:00+00:00",
            completed_at=snap.completed_at,
            resources_created_count=len(snap.resources_created),
            resources_staged_count=len(snap.resources_staged) if snap.resources_staged else 0,
            resources_failed_count=len(snap.resources_failed) if snap.resources_failed else 0,
            config_json=cfg_text,
            run_extras_json=extras_json,
        )
        stats["runs_backfilled"] += 1

        for row in snap.resources_created:
            try:
                entry = ManifestEntry.model_validate(row)
            except ValidationError:
                continue
            if not entry.created_id or entry.created_id == "SKIPPED":
                continue
            await run_artifacts.insert_created_resource_row(
                session,
                run_id=snap.run_id,
                batch=entry.batch,
                resource_type=entry.resource_type,
                typed_ref=entry.typed_ref,
                created_id=entry.created_id,
                created_at=entry.created_at,
                deletable=entry.deletable,
                child_refs=dict(entry.child_refs),
                cleanup_status=entry.cleanup_status,
            )
            stats["artifact_rows"] += 1

        for row in snap.resources_failed:
            try:
                fe = FailedEntry.model_validate(row)
            except ValidationError:
                continue
            await run_artifacts.insert_failure_row(
                session,
                run_id=snap.run_id,
                typed_ref=fe.typed_ref,
                error=fe.error,
                failed_at=fe.failed_at,
                error_type=fe.error_type,
                http_status=fe.http_status,
                error_cause=fe.error_cause,
            )
            stats["artifact_rows"] += 1

        staged_path = rdir / f"{run_id}_staged.json"
        staged_payloads: dict[str, Any] = {}
        if staged_path.is_file():
            try:
                raw_staged = json.loads(staged_path.read_text(encoding="utf-8"))
                if isinstance(raw_staged, dict):
                    staged_payloads = raw_staged
            except (json.JSONDecodeError, OSError, TypeError):
                pass

        for row in snap.resources_staged or []:
            try:
                se = StagedEntry.model_validate(row)
            except ValidationError:
                continue
            payload = staged_payloads.get(se.typed_ref, {})
            await run_artifacts.upsert_staged_item(
                session,
                run_id=snap.run_id,
                typed_ref=se.typed_ref,
                resource_type=se.resource_type,
                staged_at=se.staged_at,
                payload_json=json.dumps(payload),
            )
            stats["artifact_rows"] += 1

    return stats


async def load_runtime_correlation_from_db(session: AsyncSession) -> None:
    """Fill process-local webhook maps from ``run_created_resources`` + ``runs``."""
    cor_rows = await fetch_correlation_index_rows(session)
    org_rows = await runs_repo.fetch_run_mt_org_rows(session)
    replace_runtime_correlation_state(cor_rows, org_rows)


async def bootstrap_webhook_correlation(
    session_factory: async_sessionmaker[AsyncSession],
    runs_dir: str,
    *,
    default_user_id: int,
) -> dict[str, Any]:
    """Plan 0 startup: backfill missing runs, hydrate correlation from DB, fix unmatched rows."""
    async with session_factory() as session:
        stats = await backfill_missing_runs_from_disk(
            session,
            runs_dir,
            default_user_id=default_user_id,
        )
        await session.commit()

    if stats["runs_backfilled"] or stats["artifact_rows"]:
        logger.info(
            "DB backfill from legacy run JSON: {} new run row(s), {} artifact row(s) written",
            stats["runs_backfilled"],
            stats["artifact_rows"],
        )

    async with session_factory() as session:
        await load_runtime_correlation_from_db(session)

    async with session_factory() as session:
        recovered = await webhooks_repo.recorrelate_unmatched_webhook_events(
            session,
            correlate_inbound_payload,
        )
        await session.commit()
    if recovered:
        logger.info("Re-correlated {} previously unmatched webhook row(s)", recovered)

    n = correlation_index_size()
    logger.info("Webhook correlation index ready ({} resource IDs)", n)
    return {
        **stats,
        "index_ids": n,
        "unmatched_recovered": recovered,
    }
