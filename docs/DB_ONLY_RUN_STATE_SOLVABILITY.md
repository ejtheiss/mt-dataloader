# DB-only run state — solvability and delivery gates

This document satisfies the **solvability report** and **derisk gates** from the DB-only run state plan. Canonical architecture remains in [`RUN_STATE_STORAGE.md`](RUN_STATE_STORAGE.md).

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

## G2 — Migration rehearsal (pre-merge / deploy)

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

Record actual numbers in release notes or a pinned benchmark run when profiling.

## Review checklist — mapper boundary

- **ORM → DTO** mapping lives in `db/mappers/run_artifact_rows.py` (and repo assembly in `db/repositories/run_artifacts.py`).
- **Routers** should not construct `CreatedResourceRow` / `RunDetailView` except forwarding repo results; prefer adding a repo method over inline SQL + DTO build in a route.
