# Run state storage (DB authority)

## Source of truth

- **SQLite** (`DATALOADER_DATA_DIR` / `dataloader.sqlite`) is the **only** authority for run execution metadata and outcomes.
- Normalized tables: `run_created_resources`, `run_resource_failures`, `run_staged_items`.
- Run-level snapshot: `runs.config_json`, optional `runs.run_extras_json`; terminal status and counts on `runs`.
- **`resource_correlation`** and **`runs.manifest_json`** are removed (historical Alembic revisions only).

## DTO boundary

- HTTP handlers and Jinja templates consume **Pydantic view DTOs** in `models/run_views.py` (`RunDetailView`, `CreatedResourceRow`, …). Re-exports for BFF-only imports: `dataloader/view_models/run_detail.py`.
- **ORM → DTO** mapping for artifact rows lives in `db/mappers/run_artifact_rows.py`; `db/repositories/run_artifacts.py` runs SQL and assembles `RunDetailView`.
- SQLAlchemy ORM row types do not cross into routers or templates.
- **`execute()`** uses `dataloader/engine/execution_accumulator.py` (`ExecutionAccumulator`) for in-DAG mutable state; execution facts are `models/run_execution_entries.py` (`ManifestEntry`, …). Legacy per-run JSON on disk is parsed only by `dataloader/legacy_run_disk.py` + `dataloader/db_backfill.py` — there is no **`RunManifest`** type in the codebase.

## SSE

- **Execute** final event renders `partials/run_complete.html` with `summary: RunExecuteSummaryDTO` (counts + `has_staged`), not a manifest object.
- **Cleanup** POST snapshots `CreatedResourceRow` tuples into the ephemeral session; the stream renders those DTOs.

## Webhook correlation

- Process-local `resource_correlation_index` is hydrated at startup from `run_created_resources` rows, expanding `child_refs_json` for child MT IDs (`db/repositories/run_artifacts.fetch_correlation_index_rows`).
- `rebuild_correlation_index(runs_dir)` is a **no-op** (disk scan removed).

## Backup / ops

- **Backup run history:** copy or snapshot `dataloader.sqlite` (and retain `DATALOADER_DATA_DIR`).
- Optional JSON export of a run is a **separate** tool (not the hot path); do not treat `runs/*.json` as authoritative.

## ADR-style decisions (this cutover)


| Topic                | Decision                                                                                                                                                                         |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Counts**           | Denormalized columns on `runs`; recomputed via `runs_repo.sync_artifact_counts_from_tables` when mutating artifacts outside the execute persist port (e.g. staged fire).         |
| **Cleanup SSE**      | Snapshot DTO list at POST (deterministic, bounded session memory).                                                                                                               |
| **Webhook index**    | Derive tuples from `run_created_resources` + JSON child refs at startup (no separate index table in v1).                                                                         |
| **`run_stats` view** | **Rejected for v1** — denormalized `runs.resources_*_count` + `sync_artifact_counts_from_tables` is authoritative after mutations; a SQL view remains optional if drift is observed in ops. |
| **Mapper boundary**  | Artifact row mapping only in `db/mappers/`; routers do not build view DTOs from ORM (see checklist + `tests/test_router_dto_hygiene.py` in [`DB_ONLY_RUN_STATE_SOLVABILITY.md`](DB_ONLY_RUN_STATE_SOLVABILITY.md)). |


## Alembic

- Revision `20260420120000_db_only_run_artifacts` creates artifact tables, backfills from disk + legacy `manifest_json` / `resource_correlation`, then drops `resource_correlation` and `manifest_json`.
- Backfill reads `DATALOADER_RUNS_DIR` (default `runs`) for `*.json` manifests and staged payload files.

---

## Context7 MCP hydration (retroactive)

Hydration was run **after** the DB-only cutover using the **user-context7** MCP: `resolve-library-id` → `query-docs` for each slice below. Training data is not authoritative; these pointers match current indexed docs as returned by Context7.

### Resolved library IDs


| Topic          | Context7 `libraryId`         |
| -------------- | ---------------------------- |
| Alembic        | `/sqlalchemy/alembic`        |
| SQLAlchemy 2.0 | `/websites/sqlalchemy_en_20` |
| Pydantic v2    | `/pydantic/pydantic`         |
| FastAPI        | `/fastapi/fastapi`           |
| sse-starlette  | `/sysid/sse-starlette`       |
| pytest         | `/pytest-dev/pytest`         |
| pytest-asyncio | `/pytest-dev/pytest-asyncio` |


### Queries posed (representative)

- **Alembic:** SQLite `op.batch_alter_table(..., recreate="always")` to drop/add columns; batch vs inline `ALTER` on SQLite.
- **SQLAlchemy:** SQLite `insert().on_conflict_do_update` / `on_conflict_do_nothing`; async transaction/`Session.begin()` patterns.
- **Pydantic:** `ConfigDict(frozen=True)`; `from_attributes` + `model_validate` for ORM → DTO.
- **FastAPI:** `StreamingResponse` vs SSE; `EventSourceResponse` / `ServerSentEvent` in framework docs.
- **sse-starlette:** `EventSourceResponse` `ping`, `send_timeout`, cooperative `shutdown_event` / grace period.
- **pytest / pytest-asyncio:** `tmp_path`; `asyncio_mode` auto vs strict; `@pytest.mark.asyncio`.
- **Alembic (extra):** offline (`--sql`) vs online migrations and `context.is_offline_mode()`.

### Evidence summary (links)

1. **Alembic batch / SQLite recreate** — Batch mode groups table-level ops; `recreate='always'` forces copy-and-replace migration (required for many SQLite alters). Official batch discussion: [Alembic batch.md](https://github.com/sqlalchemy/alembic/blob/main/docs/build/batch.md), [batch_alter_table ops](https://github.com/sqlalchemy/alembic/blob/main/docs/build/ops.md).
2. **SQLite `ON CONFLICT`** — Use `sqlalchemy.dialects.sqlite.insert()` with `.on_conflict_do_update(index_elements=[...], set_=...)` or `.on_conflict_do_nothing(...)`. [SQLAlchemy 2.0 SQLite dialect](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html).
3. **Async / transactions** — `async with engine.begin() as conn:` for Core; ORM `Session.begin()` as context manager commits on success. [SQLAlchemy asyncio basic example](https://docs.sqlalchemy.org/en/20/_modules/examples/asyncio/basic.html); [Session transaction](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html).
4. **Pydantic frozen + ORM** — `model_config = ConfigDict(frozen=True)` blocks reassignment; nested mutable values (e.g. dict entries) can still change unless modeled immutably. `ConfigDict(from_attributes=True)` enables `Model.model_validate(orm_instance)`. [Pydantic models](https://github.com/pydantic/pydantic/blob/main/docs/concepts/models.md).
5. **FastAPI SSE** — Current docs cover `fastapi.sse.EventSourceResponse` and `ServerSentEvent` with `AsyncIterable`. [FastAPI SSE tutorial](https://github.com/fastapi/fastapi/blob/master/docs/en/docs/tutorial/server-sent-events.md). This repo still uses **sse-starlette** (`dataloader/routers/execute.py`, `cleanup.py`): `ping` default 15s, optional `send_timeout`, `shutdown_event` for graceful shutdown — [sse-starlette README](https://github.com/sysid/sse-starlette/blob/main/README.md).
6. **pytest-asyncio** — Configure `asyncio_mode = auto` in `pytest.ini` / `pyproject.toml` so async tests are picked up without per-test markers; CLI overrides file. Default is `strict` if unset. [pytest-asyncio configuration reference](https://github.com/pytest-dev/pytest-asyncio/blob/main/docs/reference/configuration.md).
7. **pytest `tmp_path`** — Per-test `pathlib.Path` temp directory; preferred for SQLite files in tests. [pytest tmp_path](https://github.com/pytest-dev/pytest/blob/main/doc/en/how-to/tmp_path.md).
8. **Alembic offline vs online** — Offline generates SQL (`alembic upgrade --sql`); no `alembic_version` read. Online runs against a live connection. [Alembic offline mode](https://github.com/sqlalchemy/alembic/blob/main/docs/build/offline.md).

### Follow-up (optional product/tech choices)

- **FastAPI native SSE** (`fastapi.sse`) vs **sse-starlette**: migrating would be a deliberate API/refactor; both are documented; no hydration finding forces a change while current tests pass.

---

## Solvability report and gates

See `**[DB_ONLY_RUN_STATE_SOLVABILITY.md](DB_ONLY_RUN_STATE_SOLVABILITY.md)`** for the risk/solvability matrix, migration rehearsal steps (G2), performance budget template (G3), and mapper-boundary review checklist.

For **CSS tokens and semantic styling conventions**, see **[`VISUAL_LAYER.md`](VISUAL_LAYER.md)**.

---

## Cutover status (this branch)


| Area                   | State                                                                                                                                           |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| **Authority**          | SQLite only for list, detail, execute summary, cleanup snapshots, webhook correlation index hydration.                                          |
| **Legacy columns**     | `manifest_json` / `resource_correlation` removed after migration backfill.                                                                      |
| **Disk `runs/*.json`** | Used by **Alembic backfill** and **optional** `backfill_missing_runs_from_disk` (typed via `LegacyRunDiskSnapshot` / dict parse only); not used for hot-path reads. |
| **DTO boundary**       | Routers/templates use `models/run_views.py`; ORM→DTO rows in `db/mappers/run_artifact_rows.py`; assembly in `db/repositories/run_artifacts.py`. |


## Roadmap — next slices

Ordered for dependency and risk (adjust to product priority).

### 1. Operations and confidence

- **Migration rehearsal:** on a copy of production `dataloader.sqlite`, run `alembic upgrade head`; confirm row counts in `run_created_resources` / failures / staged vs expectations; time the backfill segment on large histories.
- **Deploy ordering:** app version that understands new schema **after** migration (or same release with migration-first step); document rollback: restore DB snapshot + prior image (no forward-only data in dropped columns after upgrade).
- **Alerting:** log lines when DB session factory is missing (503 list/detail) vs query failures; optional metric for webhook correlation index size at startup.

### 2. Correlation and scale

- **Today:** full reload of correlation tuples from `run_created_resources` (+ `child_refs_json` expansion) at process start.
- **Next (if startup latency matters):** incremental updates on each persist (insert correlation rows or maintain a small side table), or lazy DB lookup on webhook with caching — trade memory vs query latency; keep tenant/run access gates identical to today.

### 3. Export and integrations

- **JSON export CLI or route (read-only):** reconstruct an aggregate JSON document from DB rows (same keys as historical `runs/<id>.json`) for archival / support; explicitly **not** authoritative and not required for runtime.
- **Headless execute:** if scheduled loads need API-only execution, add an authenticated non-browser path with the same persist + SSE (or polled job) contract; see `SCHEDULED_LOADS_RUNBOOK.md`.

### 4. SSE stack (optional)

- Evaluate **FastAPI `EventSourceResponse`** vs **sse-starlette** when touching execute/cleanup streams anyway; parity tests for disconnect, ping, and final event payload.

### 5. Hygiene

- Legacy disk filename helpers live in `dataloader/legacy_run_disk.py` (`legacy_run_json_id_from_filename`, `list_legacy_run_json_ids`, …) for backfill only.
- Keep `**plan/**` out of git; design notes that must ship with the repo belong here or in `.cursor/rules/` as needed.

## Test and verification map


| Concern                            | Tests (representative)                           |
| ---------------------------------- | ------------------------------------------------ |
| Schema + drops                     | `tests/db/test_migrations.py`                    |
| Backfill assumptions               | `tests/db/test_backfill.py`                      |
| Artifact ↔ run invariants + access | `tests/db/test_run_state_invariants.py`          |
| SSE partials + DTOs                | `tests/test_cleanup_sse_dto.py`                  |
| Execution accumulator              | `tests/dataloader/test_execution_accumulator.py` |
| Runs list SQL                      | `tests/db/test_runs_list_sql.py`                 |
| Router view-DTO hygiene            | `tests/test_router_dto_hygiene.py`               |
| Run detail SELECT budget (G3)     | `tests/db/test_run_state_invariants.py` (`test_fetch_run_detail_view_select_budget`) |
| Cleanup created-rows SELECT budget (G3) | `tests/db/test_run_state_invariants.py` (`test_fetch_cleanup_created_rows_select_budget`) |
| Migration rehearsal (G2, ops)     | [`MIGRATION_REHEARSAL_RUNBOOK.md`](MIGRATION_REHEARSAL_RUNBOOK.md) |


## Quick reference (env)

- **`DATALOADER_DATA_DIR`** — SQLite file location (see `models/settings.py`).
- **`DATALOADER_RUNS_DIR`** — disk manifests for migration/backfill tools only (`alembic` revision `20260420120000`, `db_backfill`).