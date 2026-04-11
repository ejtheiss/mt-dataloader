# Migration rehearsal (DB-only run artifacts)

Operational gate **G2** from [`DB_ONLY_RUN_STATE_SOLVABILITY.md`](DB_ONLY_RUN_STATE_SOLVABILITY.md). Run **before** upgrading production-like data or merging risky schema changes.

## When to run

- After pulling a branch that adds or changes Alembic revisions under `alembic/versions/`, especially revisions that **backfill** from disk (`DATALOADER_RUNS_DIR`) or **drop** legacy columns/tables.
- Before tagging a release that operators will apply to existing `dataloader.sqlite` files.

## Steps

1. **Copy** the target database and optional `runs/` tree (same paths your env would read: `DATALOADER_DATA_DIR`, `DATALOADER_RUNS_DIR`).
2. Record **before** counts (SQLite CLI or any client):
   - `SELECT COUNT(*) FROM runs;`
   - If pre-upgrade schema still has them: `resource_correlation`, `runs.manifest_json` presence.
3. From the repo root, with the copy’s DB URL in `sqlalchemy.url` (or symlink the copy to where Alembic expects it), run:
   - `alembic upgrade head`
4. Record **after** counts:
   - `run_created_resources`, `run_resource_failures`, `run_staged_items` row counts.
   - Spot-check a few `run_id` values: resources in UI match expectations.
5. **Skips / warnings:** capture Alembic stdout/stderr for any skipped manifest files or orphan correlation warnings.
6. **Smoke:** open **Runs**, one **Run detail**, **Listener** correlation for a known `created_id`, optional **Fire staged** in sandbox.

## Pass criteria

- Upgrade completes without unhandled exceptions.
- Artifact row counts align with expectations from disk (or documented deltas if some manifests were intentionally skipped).
- No unexplained loss of `runs` rows; `runs.status` / counts look sane for sampled runs.

## Fail / rollback

- Restore the **pre-copy** SQLite file (and prior app image if schema is incompatible).
- Document waivers in the PR if you merge with known gaps (e.g. unreadable legacy files).

## Automation

- CI already runs `alembic upgrade head` on a **fresh** temp DB before pytest (`tests/db/test_migrations.py` and related). That proves **greenfield** upgrades, not **backfill from your** `runs/` — rehearsal on a **copy** of real data remains manual until you add a dedicated job.
