# Scheduled and unattended loads

The web UI is the primary path: **Setup** → validate → **Execute** (SSE stream). There is **no** separate “batch API key” today; credentials are supplied in the browser for the active org.

## What to store

- **Config JSON** (or a saved loader draft in the DB if you use drafts).
- **Modern Treasury** sandbox (or prod) API key and organization id — treat as secrets in CI.
- **Artifact location**: run outcomes and config snapshots are persisted in **SQLite** (`DATALOADER_DATA_DIR` / `dataloader.sqlite`). See [`RUN_STATE_STORAGE.md`](RUN_STATE_STORAGE.md). Optional disk files under `runs/` are not authoritative.

## Headless / CI

Execution today **consumes a server-side session** created when you open the execute flow from a validated config. For unattended jobs:

1. **Preferred near-term:** drive the **browser** (Playwright, etc.) through validate + execute, or
2. **Scripted client:** extend the app with an authenticated `POST` that accepts config + credentials (not shipped yet — ticket if you need it).

Document whichever approach your team adopts; avoid assuming `curl` alone can hit execute without matching the session + SSE contract.

## Idempotency and repeats

- Running the **same config twice** creates **duplicate** MT resources unless the config uses reconciliation / skip semantics.
- **`on_error: skip`** records `SKIPPED` in the manifest — that is **not** the same as a successful create. Review `resources_failed` and `resources_staged`.

## Smoke checks after a scheduled run

- Open **Runs** in the UI and confirm status **completed**.
- Optional: list objects by trace metadata:

  ```bash
  cd /path/to/mt-dataloader
  PYTHONPATH=. python scripts/mt_ops.py list-by-metadata expected_payment -m your_trace_key=your_value
  ```

- Optional: point-in-time ledger balance:

  ```bash
  PYTHONPATH=. python scripts/mt_ops.py ledger-balance la_xxx --effective-at 2025-05-01T23:59:59Z
  ```

## References

- `docs/GSHEETS_LOADER_MIGRATION.md` — variable / tab mapping from the old workbook.
- `models/settings.py` — `DATALOADER_*` environment variables.
