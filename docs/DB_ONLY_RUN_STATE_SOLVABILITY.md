# DB-only run state — solvability and delivery gates

This document satisfies the **solvability report** (`solvability-report` / `adr-decisions` / design-hardening todos) and **derisk gates** from the DB-only run state plan. Canonical architecture remains in [`RUN_STATE_STORAGE.md`](RUN_STATE_STORAGE.md). **G2** has a dedicated checklist: [`MIGRATION_REHEARSAL_RUNBOOK.md`](MIGRATION_REHEARSAL_RUNBOOK.md).

## Solvability (in-scope risks)

| Risk area | Category | Solvable here? | Mitigation |
|-----------|----------|----------------|------------|
| Migration disk → normalized rows | Delivery | Yes | Alembic backfill + `tests/db/test_migrations.py` / `test_backfill.py` |
| Correlation completeness (created + child refs) | Design | Yes | Single persist path + `fetch_correlation_index_rows` + `tests/db/test_run_state_invariants.py` |
| Staged-fire atomicity | Design | Yes | `session.begin()` wraps delete staged + insert created + `sync_artifact_counts_from_tables` (`dataloader/webhooks/runs_staged.py`) |
| Run status / terminal coherence | Design | Yes | `RunStatePersistPort.finalize` + engine `_persist_finalize` |
| Access scope on artifact readers | Design | Yes | `RunAccessContext` on repo methods + `test_access_scoping_non_owner_sees_no_artifacts` |
| SSE template contract drift | Surface | Yes | DTO-only partials + `tests/test_cleanup_sse_dto.py` |
| SQLite write contention | Runtime | Mostly | WAL, single-writer SQLite; batching if profiled |
| Cleanup SSE memory vs latency | Design | Yes | **Snapshot at POST** chosen (immutable DTO list); stream-from-DB reserved if runs exceed practical session size |
| Count drift (runs counters vs child tables) | Design | Yes | **Denormalized counters** on `runs` + `sync_artifact_counts_from_tables` after non-persist mutations; optional `run_stats` view deferred (see ADR below) |
| Dedicated webhook index table | Design | Yes | **Rejected for v1** — derive index at startup from `run_created_resources` + `child_refs_json`; revisit if startup latency warrants |
| Horizontal multi-worker | Architecture | No | Out of scope for SQLite single-process contract |

**Conclusion:** In-scope risks are addressable with the current stack; open choices are documented as ADRs in [`RUN_STATE_STORAGE.md`](RUN_STATE_STORAGE.md) and this file.

## G4 — ADR lock-in (design choices recorded)

Aligned with plan **§ Derisk execution gates / G4** and **§ Remaining research checkpoints** (evidence = code + tests, not one-off benchmarks unless noted):

1. **Counts strategy:** **Denormalized** `runs.resources_*_count` columns, recomputed with `runs_repo.sync_artifact_counts_from_tables` after mutations outside the execute persist port (e.g. staged fire). **No `run_stats` view in v1** — defer unless drift appears in production metrics.
2. **Cleanup SSE strategy:** **Snapshot at POST** — immutable `tuple[CreatedResourceRow, ...]` in `SessionState.cleanup_resources` for deterministic SSE; stream-from-DB reserved for very large runs if memory becomes an issue.
3. **Webhook index strategy:** **Derive at startup** from `run_created_resources` + `child_refs_json` expansion (`fetch_correlation_index_rows`). **No `webhook_resource_index` table in v1** — revisit if startup time or memory becomes a measured bottleneck.
4. **Migration execution path:** **D1** — data backfill runs inside Alembic revision `20260420120000_db_only_run_artifacts`, reading `DATALOADER_RUNS_DIR` (default `runs`) as documented in the revision docstring; CI runs `alembic upgrade head` on a fresh DB before pytest.

Context7 hydration pointers for Alembic / SQLAlchemy / FastAPI / sse-starlette / pytest are recorded in [`RUN_STATE_STORAGE.md`](RUN_STATE_STORAGE.md) § Context7 MCP hydration.

## G2 — Migration rehearsal (pre-merge / deploy)

Step-by-step checklist: **[`MIGRATION_REHEARSAL_RUNBOOK.md`](MIGRATION_REHEARSAL_RUNBOOK.md)**.

Run against a **copy** of real `dataloader.sqlite` and optional `runs/` snapshot:

1. Record `SELECT COUNT(*)` from `runs` before upgrade.
2. `alembic upgrade head`.
3. Compare inserted counts: `run_created_resources`, `run_resource_failures`, `run_staged_items` vs expectations from disk manifests (spot-check run_ids).
4. Note Alembic log for skipped/malformed JSON files.
5. Smoke: open run detail, listener correlation, fire one staged resource in sandbox.

Merge or deploy only with a clean report or documented waivers.

## G3 — Performance budget (baseline targets)

Capture on representative hardware with a warm DB (adjust numbers after first measurement):

| Path | Target | Query expectation |
|------|--------|-------------------|
| Run detail page | &lt; 500 ms p95 server time | One access check + bounded selects for created/failures/staged (no N+1 per row) |
| Cleanup POST + first SSE row | &lt; 300 ms to first event after MT client connect | Single DB read for created rows (already ordered) |
| Runs list (HTML cap) | &lt; 400 ms p95 | Indexed SQL list query |

**Query ceiling (CI):** `tests/db/test_run_state_invariants.py::test_fetch_run_detail_view_select_budget` asserts a low **SELECT** count for `fetch_run_detail_view` (guards accidental N+1 or duplicate staged loads).

Record actual wall-clock numbers in release notes or a pinned benchmark run when profiling.

## Review checklist — mapper boundary

- **ORM → DTO** mapping lives in `db/mappers/run_artifact_rows.py` (and repo assembly in `db/repositories/run_artifacts.py`).
- **Routers** should not construct `CreatedResourceRow` / `RunDetailView` / `FailedResourceRow` / `StagedItemView` — use repositories. CI guard: `tests/test_router_dto_hygiene.py`.
