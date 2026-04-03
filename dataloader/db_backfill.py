"""Startup backfill: disk manifests → SQLite, then hydrate webhook correlation from DB."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from dataloader.engine.run_meta import list_manifest_ids, resolve_manifest_path
from dataloader.webhooks.routes import (
    correlation_index_size,
    recorrelate_unmatched_webhooks,
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


async def backfill_webhook_jsonl_to_db(session: AsyncSession, runs_dir: str | Path) -> dict[str, int]:
    """Import legacy ``*_webhooks.jsonl`` / ``_webhooks_unmatched.jsonl`` into ``webhook_events``.

    Idempotent for rows with non-empty ``webhook_id`` (``ON CONFLICT DO NOTHING``).
    """
    rdir = Path(runs_dir)
    stats = {"lines_seen": 0, "executed": 0}
    paths = list(rdir.glob("*_webhooks.jsonl"))
    unmatched = rdir / "_webhooks_unmatched.jsonl"
    if unmatched.is_file():
        paths.append(unmatched)

    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(d, dict):
                continue
            stats["lines_seen"] += 1
            raw = d.get("raw")
            if not isinstance(raw, dict):
                raw = {}
            wid = d.get("webhook_id")
            wid_s = wid.strip() if isinstance(wid, str) else None
            if not wid_s:
                continue
            await webhooks_repo.insert_webhook_event(
                session,
                webhook_id=wid_s,
                run_id=d.get("run_id") if isinstance(d.get("run_id"), str) else None,
                typed_ref=d.get("typed_ref") if isinstance(d.get("typed_ref"), str) else None,
                received_at=str(d.get("received_at", "")),
                event_type=str(d.get("event_type", "unknown")),
                resource_type=str(d.get("resource_type", "unknown")),
                resource_id=str(d.get("resource_id", "")),
                raw=raw,
            )
            stats["executed"] += 1

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
    """Plan 0 startup: backfill missing runs, hydrate correlation from DB, fix unmatched JSONL."""
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

    recovered = recorrelate_unmatched_webhooks(runs_dir)
    if recovered:
        logger.info("Re-correlated {} previously unmatched webhook(s)", recovered)

    async with session_factory() as session:
        wh_stats = await backfill_webhook_jsonl_to_db(session, runs_dir)
        await session.commit()
    if wh_stats["lines_seen"]:
        logger.info(
            "Webhook JSONL → DB: processed {} line(s), {} insert attempt(s)",
            wh_stats["lines_seen"],
            wh_stats["executed"],
        )

    n = correlation_index_size()
    logger.info("Webhook correlation index ready ({} resource IDs)", n)
    return {
        **stats,
        "index_ids": n,
        "unmatched_recovered": recovered,
        "webhook_jsonl_lines_seen": wh_stats["lines_seen"],
        "webhook_db_inserts_attempted": wh_stats["executed"],
    }
