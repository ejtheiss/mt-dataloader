# Modern Treasury Dataloader

Upload a JSON **DataLoaderConfig** in the browser: the app validates it, shows execution order (DAG), and creates resources in Modern Treasury’s **sandbox** via the Python SDK, with live progress (SSE).

---

## Quick start

**You need:** Python 3.11+, a Modern Treasury **sandbox** API key and org ID.

```bash
cd mt-dataloader
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Open **http://127.0.0.1:8000**. Enter your **API key** and **org ID** on the setup screen, upload JSON (or paste), then **Validate → Preview → Execute**. No `.env` file is required.

---

## Configuration (everything optional)

| What | Default behavior |
|------|------------------|
| **MT credentials** | Enter in the web UI when you run a flow. Stored for that session; you can skip `.env` entirely. |
| **`.env`** | Optional. Copy `.env.example` → `.env` only if you want the server to pre-fill defaults (e.g. key/org so you don’t type them each time). |
| **`baseline.yaml`** | Optional fallback when **org discovery** can’t reach MT. Most self-contained configs define their own `connections` and `internal_accounts` and don’t rely on baseline. |
| **Runs / logs** | Written under `runs/` and `logs/` (created automatically). Override with `DATALOADER_RUNS_DIR` if you care. |

Other knobs (`DATALOADER_LOG_LEVEL`, `DATALOADER_MAX_CONCURRENT_REQUESTS`, etc.) are optional; see `.env.example` or `AppSettings` in `models.py`.

**Org discovery:** On validate, the app may query your org for existing connections / accounts / ledgers and register refs. If that fails (e.g. timeout), it falls back to `baseline.yaml`. Auth errors are not masked.

---

## JSON config

- **Schema (for LLMs / tools):** `GET /api/schema` — full `DataLoaderConfig` JSON Schema.
- **Validate without UI:** `POST /api/validate-json` — body = raw JSON; returns structured errors for repair loops.

Resources reference each other with **`$ref:<resource_type>.<ref>`** (e.g. `$ref:internal_account.buyer_wallet`). The `ref` field on each object is a short key; the engine builds the typed name. Child refs include selectors like `$ref:counterparty.vendor_cp.account[0]`.

See **`prompts/`** — start with **`prompts/README.md`** (what each file is for) and **`prompts/system_prompt.md`** (output format + paste order). Use the two files under **`examples/`** as structural templates for PSP shapes.

---

## Examples

| File | What it shows |
|------|----------------|
| `examples/marketplace_demo.json` | Full PSP marketplace: legal entities, counterparties, wallet internal accounts, sandbox **IPD** (inbound simulation), **book** settle + fee + **ACH** payout, plus an **ACH debit** + `sandbox_behavior: "return"` NSF demo. No ledger, no expected payment, no virtual account. |
| `examples/psp_minimal.json` | Smallest useful **book** transfer between two internal accounts. |

Validate examples locally:

```bash
source .venv/bin/activate
python - <<'PY'
import json
from models import DataLoaderConfig
from engine import dry_run
for p in ("examples/marketplace_demo.json", "examples/psp_minimal.json"):
    with open(p) as f:
        dry_run(DataLoaderConfig(**json.load(f)))
    print(p, "OK")
PY
```

---

## Execution flow

1. **Validate** — Credentials check, optional discovery, parse JSON, build DAG, dry run.
2. **Preview** — Batches, dependencies, metadata, cleanup hints.
3. **Execute** — Topological order, SSE updates, idempotency keys on creates.
4. **Runs** — Manifests, cleanup (delete/archive what the API allows).

---

## Cleanup

| Action | Typical resources |
|--------|-------------------|
| Delete | Counterparties, external/virtual accounts, ledgers, ledger accounts, categories, expected payments |
| Archive | Ledger transactions |
| Remove | Category / nested category links |
| Skip | Internal accounts, legal entities, payment orders, returns, reversals, connections |

---

## Layout

```
main.py, models.py, engine.py, handlers.py, baseline.py
templates/     HTMX + Jinja2 UI
static/        CSS
examples/      marketplace_demo.json, psp_minimal.json
prompts/       LLM kit + system_prompt.md
baseline.yaml  discovery fallback
runs/, logs/   runtime (gitignored)
```

---

## Development

| Module | Role |
|--------|------|
| `models.py` | Pydantic config + `AppSettings` |
| `engine.py` | Refs, DAG (`graphlib`), execute, manifests |
| `handlers.py` | MT SDK calls, polling |
| `baseline.py` | Org discovery + YAML fallback |
| `main.py` | FastAPI, SSE, cleanup |

```bash
source .venv/bin/activate
python test_step6_smoke.py
```

---

## Scope

**In:** Sandbox resource creation from JSON, `$ref` DAG, SSE UI, run manifests + idempotency, metadata passthrough.

**Out:** Embedded LLM, production attach-to-arbitrary-org mode, webhooks, full CLI.
