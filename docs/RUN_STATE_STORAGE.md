# Run state storage (DB authority)

## Source of truth

- **SQLite** (`DATALOADER_DATA_DIR` / `dataloader.sqlite`) is the **only** authority for run execution metadata and outcomes.
- Normalized tables: `run_created_resources`, `run_resource_failures`, `run_staged_items`.
- Run-level snapshot: `runs.config_json`, optional `runs.run_extras_json`; terminal status and counts on `runs`.
- **`resource_correlation` and `runs.manifest_json` are removed** (historical Alembic revisions only).

## DTO boundary

- HTTP handlers and Jinja templates consume **Pydantic view DTOs** in `models/run_views.py` (`RunDetailView`, `CreatedResourceRow`, …), built in `db/repositories/run_artifacts.py`.
- SQLAlchemy ORM types do not cross the repository boundary into routers or templates.

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

| Topic | Decision |
|-------|----------|
| **Counts** | Denormalized columns on `runs`; recomputed via `runs_repo.sync_artifact_counts_from_tables` when mutating artifacts outside the execute persist port (e.g. staged fire). |
| **Cleanup SSE** | Snapshot DTO list at POST (deterministic, bounded session memory). |
| **Webhook index** | Derive tuples from `run_created_resources` + JSON child refs at startup (no separate index table in v1). |

## Alembic

- Revision `20260420120000_db_only_run_artifacts` creates artifact tables, backfills from disk + legacy `manifest_json` / `resource_correlation`, then drops `resource_correlation` and `manifest_json`.
- Backfill reads `DATALOADER_RUNS_DIR` (default `runs`) for `*.json` manifests and staged payload files.

---

## Context7 MCP hydration (retroactive)

Hydration was run **after** the DB-only cutover using the **user-context7** MCP: `resolve-library-id` → `query-docs` for each slice below. Training data is not authoritative; these pointers match current indexed docs as returned by Context7.

### Resolved library IDs

| Topic | Context7 `libraryId` |
|-------|----------------------|
| Alembic | `/sqlalchemy/alembic` |
| SQLAlchemy 2.0 | `/websites/sqlalchemy_en_20` |
| Pydantic v2 | `/pydantic/pydantic` |
| FastAPI | `/fastapi/fastapi` |
| sse-starlette | `/sysid/sse-starlette` |
| pytest | `/pytest-dev/pytest` |
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

1. **Alembic batch / SQLite recreate** — Batch mode groups table-level ops; `recreate='always'` forces copy-and-replace migration (required for many SQLite alters). Official batch discussion: [Alembic `batch.md`](https://github.com/sqlalchemy/alembic/blob/main/docs/build/batch.md), [`batch_alter_table` ops](https://github.com/sqlalchemy/alembic/blob/main/docs/build/ops.md).

2. **SQLite `ON CONFLICT`** — Use `sqlalchemy.dialects.sqlite.insert()` with `.on_conflict_do_update(index_elements=[...], set_=...)` or `.on_conflict_do_nothing(...)`. [SQLAlchemy 2.0 SQLite dialect](https://docs.sqlalchemy.org/en/20/dialects/sqlite.html).

3. **Async / transactions** — `async with engine.begin() as conn:` for Core; ORM `Session.begin()` as context manager commits on success. [SQLAlchemy asyncio basic example](https://docs.sqlalchemy.org/en/20/_modules/examples/asyncio/basic.html); [Session transaction](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html).

4. **Pydantic frozen + ORM** — `model_config = ConfigDict(frozen=True)` blocks reassignment; nested mutable values (e.g. dict entries) can still change unless modeled immutably. `ConfigDict(from_attributes=True)` enables `Model.model_validate(orm_instance)`. [Pydantic models](https://github.com/pydantic/pydantic/blob/main/docs/concepts/models.md).

5. **FastAPI SSE** — Current docs cover `fastapi.sse.EventSourceResponse` and `ServerSentEvent` with `AsyncIterable`. [FastAPI SSE tutorial](https://github.com/fastapi/fastapi/blob/master/docs/en/docs/tutorial/server-sent-events.md). This repo still uses **`sse-starlette`** (`dataloader/routers/execute.py`, `cleanup.py`): `ping` default 15s, optional `send_timeout`, `shutdown_event` for graceful shutdown — [sse-starlette README](https://github.com/sysid/sse-starlette/blob/main/README.md).

6. **pytest-asyncio** — Configure `asyncio_mode = auto` in `pytest.ini` / `pyproject.toml` so async tests are picked up without per-test markers; CLI overrides file. Default is `strict` if unset. [pytest-asyncio configuration reference](https://github.com/pytest-dev/pytest-asyncio/blob/main/docs/reference/configuration.md).

7. **pytest `tmp_path`** — Per-test `pathlib.Path` temp directory; preferred for SQLite files in tests. [pytest tmp_path](https://github.com/pytest-dev/pytest/blob/main/doc/en/how-to/tmp_path.md).

8. **Alembic offline vs online** — Offline generates SQL (`alembic upgrade --sql`); no `alembic_version` read. Online runs against a live connection. [Alembic offline mode](https://github.com/sqlalchemy/alembic/blob/main/docs/build/offline.md).

### Follow-up (optional product/tech choices)

- **FastAPI native SSE** (`fastapi.sse`) vs **sse-starlette**: migrating would be a deliberate API/refactor; both are documented; no hydration finding forces a change while current tests pass.
