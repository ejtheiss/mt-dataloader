"""Startup backfill: disk manifests → SQLite, then hydrate webhook correlation from DB."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dataloader.engine.run_meta import list_manifest_ids, resolve_manifest_path
from dataloader.webhooks.correlation_state import (
    correlate_inbound_payload,
    correlation_index_size,
    replace_runtime_correlation_state,
)
from db.repositories import correlation as correlation_repo
from db.repositories import runs as runs_repo
from db.repositories import webhooks as webhooks_repo
from models import RunManifest


async def backfill_missing_runs_from_disk(
    session: AsyncSession,
    runs_dir: str | Path,
    *,
    default_user_id: int,
) -> dict[str, int]:
    """Upsert ``runs`` + ``resource_correlation`` for manifests with no ``runs`` row yet.

    Idempotent: skips ``run_id`` values already present in ``runs``.
    """
    rdir = Path(runs_dir)
    stats = {"runs_backfilled": 0, "correlations_upserted": 0}
    existing = await runs_repo.list_run_id_set(session)

    for run_id in list_manifest_ids(rdir):
        if run_id in existing:
            continue
        path = resolve_manifest_path(rdir, run_id)
        if path is None:
            logger.bind(run_id=run_id).warning("backfill: manifest path not found, skipping")
            continue
        try:
            manifest = RunManifest.load(path)
        except Exception as exc:
            logger.bind(run_id=run_id, path=str(path)).warning("backfill: load failed: {}", exc)
            continue

        await runs_repo.backfill_upsert_run(
            session,
            run_id=manifest.run_id,
            user_id=default_user_id,
            mt_org_id=manifest.mt_org_id,
            mt_org_label=manifest.mt_org_label,
            status=manifest.status,
            config_hash=manifest.config_hash or None,
            started_at=manifest.started_at,
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
        stats["runs_backfilled"] += 1

        for entry in manifest.resources_created:
            await correlation_repo.upsert_correlation(
                session,
                created_id=entry.created_id,
                run_id=manifest.run_id,
                typed_ref=entry.typed_ref,
            )
            stats["correlations_upserted"] += 1
            for child_key, child_id in entry.child_refs.items():
                await correlation_repo.upsert_correlation(
                    session,
                    created_id=child_id,
                    run_id=manifest.run_id,
                    typed_ref=f"{entry.typed_ref}.{child_key}",
                )
                stats["correlations_upserted"] += 1

    return stats


async def load_runtime_correlation_from_db(session: AsyncSession) -> None:
    """Fill process-local webhook maps from ``resource_correlation`` + ``runs``."""
    cor_rows = await correlation_repo.fetch_correlation_rows(session)
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

    if stats["runs_backfilled"] or stats["correlations_upserted"]:
        logger.info(
            "DB backfill from manifests: {} new run row(s), {} correlation row(s) written",
            stats["runs_backfilled"],
            stats["correlations_upserted"],
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
