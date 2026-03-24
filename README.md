# Modern Treasury Dataloader

Upload a JSON **DataLoaderConfig** in the browser: the app validates it, shows execution order (DAG), and creates resources in Modern Treasury's **sandbox** via the Python SDK, with live progress (SSE). Includes a **Funds Flows DSL** for defining multi-step payment lifecycles, a compile-time preview UI, Mermaid sequence diagrams, and a generation pipeline for scaling one pattern to hundreds of instances.

---

## Quick start

**You need:** Python 3.11+, a Modern Treasury **sandbox** API key and org ID.

```bash
cd mt-dataloader
make setup                         # creates .venv, installs deps
source .venv/bin/activate          # Windows: .venv\Scripts\activate
make run                           # starts uvicorn with auto-reload
```

Or manually: `python3 -m venv .venv && pip install -r requirements.txt && uvicorn main:app --reload`

Open **http://127.0.0.1:8000**. Enter your **API key** and **org ID** on the setup screen, upload JSON (or paste), then **Validate**. If the config uses **Funds Flows**, you'll see the Fund Flows view first (lifecycle visualization, Mermaid diagrams, generation controls) before proceeding to **Preview / Execute**. No `.env` file is required.

---

## Configuration (everything optional)

| What | Default behavior |
|------|------------------|
| **MT credentials** | Enter in the web UI when you run a flow. Stored for that session; you can skip `.env` entirely. |
| **`.env`** | Optional. Copy `.env.example` -> `.env` only if you want the server to pre-fill defaults (e.g. key/org so you don't type them each time). |
| **`baseline.yaml`** | Optional fallback when **org discovery** can't reach MT. Most self-contained configs define their own `connections` and `internal_accounts` and don't rely on baseline. |
| **Runs / logs** | Written under `runs/` and `logs/` (created automatically). Override with `DATALOADER_RUNS_DIR` if you care. |

Other knobs (`DATALOADER_LOG_LEVEL`, `DATALOADER_MAX_CONCURRENT_REQUESTS`, etc.) are optional; see `.env.example` or `AppSettings` in `models.py`.

**Org discovery:** On validate, the app may query your org for existing connections / accounts / ledgers and register refs. If that fails (e.g. timeout), it falls back to `baseline.yaml`. Auth errors are not masked.

---

## Funds Flows DSL

The `funds_flows` section in a config defines multi-step payment lifecycles declaratively. The compiler transforms them into the standard `DataLoaderConfig` resource arrays.

```json
{
  "funds_flows": [{
    "ref": "deposit_settle",
    "pattern_type": "deposit_settle",
    "trace_key": "deal_id",
    "trace_value_template": "deal-{ref}-{instance}",
    "actors": { "ops": "$ref:internal_account.ops_usd" },
    "steps": [
      { "step_id": "ipd", "type": "incoming_payment_detail", "payload": { ... } },
      { "step_id": "fee", "type": "payment_order", "depends_on": ["ipd"], "payload": { ... } }
    ],
    "optional_groups": [{
      "label": "ach_return",
      "probability": 0.05,
      "steps": [{ "step_id": "return_ipd", "type": "return", "payload": { ... } }]
    }]
  }]
}
```

**Key concepts:**

- **Trace metadata** (`trace_key` / `trace_value_template`) stamps every resource for grouping in MT's UI
- **`@actor:alias`** in payloads resolves to the actor ref at compile time
- **`optional_groups`** model lifecycle variants (returns, reversals, NSF) with a probability that controls activation during generation
- **Ledger transaction lifecycle** supports `ledger_status`, `ledger_inline`, and `transition_ledger_transaction` steps

### Generation pipeline

The **scenario builder** on the Fund Flows page scales one pattern to N instances:

- **GenerationRecipeV1** controls: `instances`, `seed` (deterministic RNG), `seed_dataset`, `edge_case_frequency`, `amount_variance_pct`, `staged_count`, `staged_selection`, `payment_mix`
- **Seed datasets** (10 available): pure Faker ("standard"), 6 industry verticals (tech, government, payroll, manufacturing, property_management, construction), and 3 pop-culture (harry_potter, superheroes, seinfeld). Selectable from the scenario builder UI.
- **`instance_resources`** on `FundsFlowConfig` defines per-instance infrastructure templates (LEs, CPs, IAs, LAs) that are cloned with `{first_name}`, `{last_name}`, `{business_name}`, `{instance}` substitution from seed profiles
- Edge cases activate `optional_groups` probabilistically per the recipe
- Compile-time preview shows resource counts and estimated API calls before execution

### Fund Flows UI

- **`/flows`** -- List of compiled flows with diagnostics bar, Mermaid sequence diagram accordions (copy syntax / SVG), scenario builder, and JSON editor
- **`/flows/view/<idx>`** -- Detail view with a multi-column scroll-synced T-account layout (transactions left, ledger account debits/credits right), edge case badges, and per-flow Mermaid diagram

### Mermaid diagrams

Each compiled flow generates a Mermaid `sequenceDiagram` showing actors, message arrows by payment type, and `opt` blocks for optional groups. Diagrams render client-side via Mermaid.js and can be copied as syntax or SVG.

---

## JSON config

- **Schema (for LLMs / tools):** `GET /api/schema` -- full `DataLoaderConfig` JSON Schema.
- **Validate without UI:** `POST /api/validate-json` -- body = raw JSON; returns structured errors for repair loops.

Resources reference each other with **`$ref:<resource_type>.<ref>`** (e.g. `$ref:internal_account.buyer_maya_wallet`). The `ref` field on each object is a short key; the engine builds the typed name. Child refs include selectors like `$ref:counterparty.vendor_cp.account[0]`.

**Legal entities (sandbox):** For demos, you only need `ref`, `legal_entity_type`, and name fields in JSON. The app **replaces** identifications, addresses, documents, and related compliance fields with deterministic mock data before calling MT, so sandbox KYC/KYB stays predictable.

**Connections (sandbox):** Use **`entity_id: "example1"`** or **`"example2"`** on `connections` when the flow includes **ACH or wire** payment orders on newly created internal accounts. The **`modern_treasury`** entity is effectively **book-only** for new IAs in sandbox; ACH POs will 422. See `prompts/decision_rubrics.md` (Connections).

After creating a legal entity, the engine **polls** until MT reports `active` (or timeout) before continuing, so dependent internal accounts are less likely to race pending compliance.

See **`prompts/`** -- start with **`prompts/README.md`** (what each file is for) and **`prompts/system_prompt.md`** (output format + paste order). Use the files under **`examples/`** as structural templates.

---

## Webhooks (optional)

Receive real-time MT webhook events correlated to dataloader runs.

The dataloader runs on `localhost:8000`, but Modern Treasury needs to reach it over the internet to deliver webhooks. **[ngrok](https://ngrok.com)** creates a temporary public URL that tunnels traffic to your local machine -- MT sends a webhook to the public URL, ngrok forwards it to `localhost:8000`, and the dataloader receives it.

### 1. Install ngrok (one-time)

```bash
brew install ngrok                          # macOS
# or: https://ngrok.com/download            # other platforms
```

Create a **free account** at [ngrok.com](https://ngrok.com/signup), then authenticate (your auth token is on the ngrok dashboard):

```bash
ngrok config add-authtoken <your-token>
```

### 2. Start the tunnel

With the dataloader already running (`make run`), open a **second terminal**:

```bash
make tunnel
# or: ngrok http 8000
```

ngrok prints a forwarding URL:

```
Forwarding  https://ab12-34-56.ngrok-free.app -> http://localhost:8000
```

That `https://...ngrok-free.app` URL is your tunnel. It changes every time you restart ngrok (free plan). Paid plans support stable subdomains.

### 3. Create a webhook endpoint in Modern Treasury

Go to **MT Dashboard -> Developers -> Webhooks -> Add Endpoint**.

In the **Webhook URL** field, paste your ngrok URL with `/webhooks/mt` appended:

```
https://ab12-34-56.ngrok-free.app/webhooks/mt
```

> **Important:** Do NOT use `localhost:8000` -- MT's servers cannot reach your machine at that address. You must use the `https://` URL from ngrok. The `/webhooks/mt` path at the end is the dataloader's receiver endpoint and is always the same.

> **Tip:** Open **http://127.0.0.1:8000/listen** -- the dataloader auto-detects your ngrok tunnel and displays the full webhook URL ready to copy.

Set the remaining fields:

| Field | Value |
|-------|-------|
| **Basic Authentication** | Disabled |
| **Events to send** | "Receive all events" (recommended) or select specific types |

Click **Save**. MT displays a **signing secret** -- copy it if you want signature verification (step 4).

### 4. Configure signature verification (optional)

Add the signing secret to `.env`:

```bash
DATALOADER_WEBHOOK_SECRET=whsec_...
```

Without it the receiver accepts all payloads -- fine for sandbox demos. With it, the receiver validates the HMAC-SHA256 signature on every request and rejects tampered payloads.

### 5. Verify it works

Open **http://127.0.0.1:8000/listen** and click **Send Test** -- a synthetic event should appear in the live feed. Then run a config and watch real MT events stream in on both the listener page and the run detail page (**Runs -> Details -> Webhooks** tab).

### Quick reference

```bash
# Terminal 1 -- start the app
make run

# Terminal 2 -- start the tunnel
make tunnel
```

Then open **http://127.0.0.1:8000/listen** to see the tunnel URL and webhook feed.

Run `make help` to see all available commands.

### Staged resources (live demo mode)

Four resource types support `staged: true`: **payment orders**, **incoming payment details**, **expected payments**, and **ledger transactions**. Staged resources are resolved (refs replaced with real IDs) but **not created** during execution. They appear as "Fire" buttons on the run detail page so you can trigger them one-by-one during a live demo while webhook events stream in.

See `examples/staged_demo.json` for a working example and `prompts/decision_rubrics.md` (Staged Resources) for dependency rules.

---

## Examples

| File | What it shows |
|------|----------------|
| `examples/marketplace_demo.json` | Full PSP marketplace: `modern_treasury_bank` + `example1`, legal entities, counterparties, internal accounts (`*_wallet` refs, **Payment Account** MT names), sandbox **IPD**, **book** fee + settle + **ACH** payout, **ACH debit** NSF demo (`sandbox_behavior`). No ledger / EP / VA. |
| `examples/psp_minimal.json` | Smallest useful **book** transfer between two internal accounts. |
| `examples/staged_demo.json` | Marketplace with `staged: true` on IPD + 3 POs. Infrastructure creates normally; staged items get "Fire" buttons. Deposit -> fee -> settle -> payout chain. |
| `examples/funds_flow_demo.json` | **Funds Flows DSL** example with `funds_flows` section, actors, steps, and `optional_groups` for lifecycle variants. Demonstrates the compile-time preview and Mermaid rendering. |
| `examples/tradeify.json` | **Ledger-heavy PSP.** Brokerage funding: 10 users, ledger with chart-of-accounts, standalone `ledger_transactions` (2-leg seed, 4-leg reallocation, payout journal entries), RTP POs. |

Validate examples locally:

```bash
source .venv/bin/activate
python - <<'PY'
import json
from models import DataLoaderConfig
from engine import dry_run
for p in ("examples/marketplace_demo.json", "examples/psp_minimal.json", "examples/staged_demo.json"):
    with open(p) as f:
        dry_run(DataLoaderConfig(**json.load(f)))
    print(p, "OK")
PY
```

---

## Execution flow

1. **Validate** -- Credentials check, optional discovery, parse JSON, compile funds flows (if present), build DAG, dry run.
2. **Fund Flows** (if `funds_flows` present) -- Diagnostics, Mermaid diagrams, scenario builder for generation, JSON editor.
3. **Preview** -- Batches, dependencies, metadata, cleanup hints. Edge case badges on resources from optional groups. Filter/sort/search/export.
4. **Execute** -- Topological order, SSE updates, idempotency keys on creates. Staged resources are resolved but held back.
5. **Run detail** -- Config viewer, resource list, staged "Fire" buttons, live + historical webhooks (four tabs).
6. **Runs** -- Manifests, cleanup (delete/archive what the API allows).

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
main.py              FastAPI routes, session management, SSE
models.py            Pydantic config schemas (DataLoaderConfig, FundsFlowConfig, GenerationRecipeV1)
engine.py            Refs, DAG (graphlib), execute, run manifests
handlers.py          MT SDK calls, polling, metadata stripping
flow_compiler.py     Funds Flow DSL -> FlowIR -> DataLoaderConfig compiler, Mermaid rendering
seed_loader.py       Faker hybrid seed engine (standard, industry, pop-culture)
baseline.py          Org discovery + YAML fallback
webhooks.py          Webhook receiver, run detail, staged fire, listener

Makefile             setup, run, tunnel, validate shortcuts
templates/           HTMX + Jinja2 UI
  partials/          Reusable fragments (mermaid, diagnostics, scenario builder, resource rows)
static/              CSS
examples/            marketplace_demo, psp_minimal, staged_demo, funds_flow_demo, tradeify
prompts/             LLM prompt kit (system_prompt, decision_rubrics, ChatGPT instructions)
seeds/               Seed catalog (business/individual profiles for generation)
baseline.yaml        Discovery fallback
runs/, logs/         Runtime (gitignored)
tests/               Pytest suite (262 tests)
```

---

## Development

| Module | Role |
|--------|------|
| `models.py` | Pydantic config + `AppSettings` + `FundsFlowConfig` + `GenerationRecipeV1` |
| `engine.py` | Refs, DAG (`graphlib`), execute, manifests |
| `handlers.py` | MT SDK calls, polling, trace metadata stripping |
| `flow_compiler.py` | `compile_to_plan()`, `compile_flows()`, `emit_dataloader_config()`, `render_mermaid()`, generation pipeline |
| `seed_loader.py` | Faker hybrid seed engine: 10 datasets (standard/industry/pop-culture), `generate_profiles()`, `pick_profile()` |
| `baseline.py` | Org discovery + YAML fallback |
| `main.py` | FastAPI, SSE, Fund Flows UI routes, cleanup |
| `webhooks.py` | Webhook receiver, run detail, staged fire, listener |

```bash
source .venv/bin/activate
python -m pytest tests/ --ignore=tests/test_step6_smoke.py -q
```

---

## Scope

**In:** Sandbox resource creation from JSON, `$ref` DAG, SSE UI, run manifests + idempotency, metadata passthrough, webhook receiver + correlation, staged resources with live-fire UI, Funds Flows DSL (compiler, Mermaid rendering, generation pipeline, scenario builder), compile-time preview with T-account layout.

**Out:** Embedded LLM, production attach-to-arbitrary-org mode, full CLI.
