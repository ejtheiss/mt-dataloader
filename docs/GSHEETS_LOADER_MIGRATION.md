# Migrating from the Google Sheets data loader

This note maps familiar spreadsheet concepts to the Modern Treasury dataloader repo. For the full parity plan (including tooling and UI), see `plan/gsheets-loader-parity-and-improvements.md` locally.

## `MTVARIABLE_DICT` / “Data Generation - Variables”

| Old idea (sheet key) | Where it lives now |
|---------------------|-------------------|
| Row counts, list sizes, simulation scale | `GenerationRecipeV1`: `count`, `seed`, `seed_dataset`, per-actor overrides (`models/flow_dsl.py`) |
| Random but reproducible names | `flow_compiler` + `seed_loader` + Faker; see `flow_compiler/seeds/README.md` |
| Company / product labels, constants | Fields on `DataLoaderConfig`, `funds_flows` steps, or `metadata` on resources |
| Cohort / deal tagging for search in MT | `trace_key`, `trace_value_template`, optional `trace_metadata` and per-step metadata (flows UI + `/api/flows/{idx}/metadata`) |

## Sheet tabs vs this app

| Spreadsheet tab | This repo |
|-----------------|-----------|
| LTs / payroll staging rows with JSON columns | `funds_flows` → compiled `ledger_transactions` (and related resources); validate in **Setup** |
| Data Generation - Lists | Seed YAML + recipe-driven generation |
| Data Generation - Groups (LA / bank / metadata per group) | Resource arrays in JSON (`ledger_account`, `internal_account`, `counterparty`, …) |
| Category dictionaries | `ledger_account_categories` and modeled account fields; `flow_validator` + Pydantic |
| History Tool (curl, payloads, balances) | **Runs** (history from **SQLite only** — not `runs/*.json`), **Execute** SSE stream, **Listener**; CLI `scripts/mt_ops.py` for list-by-metadata and ledger balance — see [`RUN_STATE_STORAGE.md`](RUN_STATE_STORAGE.md) |

## Many payroll rows (N employees)

Prefer **`GenerationRecipeV1` + `funds_flows`** (or `instance_resources`) to duplicate a whole sheet per scenario — one pattern, many instances, deterministic `seed`.
