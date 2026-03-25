# Modern Treasury Dataloader

Upload a JSON **DataLoaderConfig** in the browser: the app validates it, shows execution order (DAG), and creates resources in Modern Treasury's **sandbox** via the Python SDK, with live progress (SSE). Includes a **Funds Flows DSL** for defining multi-step payment lifecycles, a compile-time preview UI, Mermaid sequence diagrams, and a generation pipeline for scaling one pattern to hundreds of instances.

---

## Quick start

### Option A: Docker (recommended)

**You need:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) and a Modern Treasury **sandbox** API key and org ID.

```bash
git clone <repo-url> && cd mt-dataloader
cp .env.example .env               # fill in your API key + org ID
make docker-build                  # build the image
make docker-run                    # start the container
```

Open **http://localhost:8000**. That's it. Stop with `make docker-stop`.

### Option B: Local Python

**You need:** Python 3.11+, a Modern Treasury **sandbox** API key and org ID.

```bash
cd mt-dataloader
make setup                         # creates .venv, installs deps
source .venv/bin/activate          # Windows: .venv\Scripts\activate
make run                           # starts uvicorn with auto-reload
```

Or manually: `python3 -m venv .venv && pip install -r requirements.txt && uvicorn main:app --reload`

---

Open **http://127.0.0.1:8000**. Enter your **API key** and **org ID** on the setup screen, upload JSON (or paste), then **Validate**. If the config uses **Funds Flows**, you'll see the Fund Flows view first (lifecycle visualization, Mermaid diagrams, generation controls) before proceeding to **Preview / Execute**. No `.env` file is required — credentials can be entered in the UI.

---

## Configuration (everything optional)

| What | Default behavior |
|------|------------------|
| **MT credentials** | Enter in the web UI when you run a flow. Stored for that session; you can skip `.env` entirely. |
| **`.env`** | Optional. Copy `.env.example` -> `.env` only if you want the server to pre-fill defaults (e.g. key/org so you don't type them each time). |
| **Runs / logs** | Written under `runs/` and `logs/` (created automatically). Override with `DATALOADER_RUNS_DIR` if you care. |

Other knobs (`DATALOADER_LOG_LEVEL`, `DATALOADER_MAX_CONCURRENT_REQUESTS`, etc.) are optional; see `.env.example` or `AppSettings` in `models/settings.py`.

**Org discovery:** On validate, the app queries your org for existing connections, accounts, ledgers, legal entities, and counterparties. Matching resources are registered in the ref registry so they aren't re-created. Reconciliation shows matches and lets you remap refs to existing resources. Auth errors are not masked.

---

## Funds Flows DSL

The `funds_flows` section in a config defines multi-step payment lifecycles declaratively. The compiler transforms them into the standard `DataLoaderConfig` resource arrays.

```json
{
  "funds_flows": [{
    "ref": "simple_deposit",
    "pattern_type": "deposit_settle",
    "trace_key": "deal_id",
    "trace_value_template": "deal-{ref}-{instance}",
    "actors": {
      "direct_1": {
        "alias": "Customer",
        "frame_type": "direct",
        "customer_name": "Demo Customer",
        "slots": { "bank": "$ref:counterparty.demo_customer.account[0]" }
      },
      "direct_2": {
        "alias": "Platform",
        "frame_type": "direct",
        "customer_name": "Demo Corp",
        "slots": {
          "ops": "$ref:internal_account.ops_usd",
          "cash": "$ref:ledger_account.cash"
        }
      }
    },
    "steps": [
      {
        "step_id": "deposit", "type": "incoming_payment_detail",
        "payment_type": "ach", "direction": "credit", "amount": 50000,
        "originating_account_id": "@actor:direct_1.bank",
        "internal_account_id": "@actor:direct_2.ops"
      },
      {
        "step_id": "settle", "type": "ledger_transaction",
        "depends_on": ["deposit"],
        "ledger_entries": [
          { "ledger_account_id": "@actor:direct_2.cash", "amount": 50000, "direction": "debit" },
          { "ledger_account_id": "@actor:direct_2.cash", "amount": 50000, "direction": "credit" }
        ]
      }
    ],
    "optional_groups": [{
      "label": "Customer requests return",
      "steps": [{ "step_id": "return_deposit", "type": "return", "depends_on": ["deposit"] }]
    }]
  }]
}
```

**Key concepts:**

- **Actor frames** define participants: `user` frames get faker-seeded identities (per-actor entity type, dataset, name template); `direct` frames take a literal `customer_name`. Each frame has named `slots` mapping to account/resource refs.
- **`@actor:alias.slot`** in step fields resolves to the actor's slot ref at compile time
- **Trace metadata** (`trace_key` / `trace_value_template`) stamps every resource for grouping in MT's UI
- **`optional_groups`** model lifecycle variants (returns, reversals, alternative payout methods) with discrete counts controlling how many instances get each edge case
- **`position` / `insert_after`** on optional groups controls placement: `"after"` appends, `"before"` prepends, `"replace"` swaps out the anchor step (used for alternative payment methods in an `exclusion_group`)
- **Ledger transaction lifecycle** supports `ledger_status`, `ledger_inline`, and `transition_ledger_transaction` steps

### Generation pipeline

The **scenario builder** on the Fund Flows page scales one pattern to N instances:

- **GenerationRecipeV1** controls: `instances`, `seed` (deterministic RNG), `seed_dataset`, `edge_case_count` (per-group discrete counts), `amount_variance_pct`, `staged_count`, `staged_selection`, `payment_mix`, `actor_overrides`
- **Per-actor identity**: each actor frame can be configured independently — entity type (business/individual), seed dataset, or a literal customer name. Cascading dataset dropdowns hide industry verticals for individuals.
- **Seed datasets** (10 available): pure Faker ("standard"), 6 industry verticals (tech, government, payroll, manufacturing, property_management, construction), and 3 pop-culture (harry_potter, superheroes, seinfeld). Selectable per actor from the scenario builder UI.
- **`instance_resources`** on `FundsFlowConfig` defines per-instance infrastructure templates (LEs, CPs, IAs, LAs, category memberships) that are cloned with `{first_name}`, `{last_name}`, `{business_name}`, `{instance}` substitution from seed profiles
- **Edge cases** activate `optional_groups` by discrete count — each group gets an exact number of instances, with mutually exclusive groups (via `exclusion_group`) distributing counts by weight
- **Staged selection** stages instances from: `"happy_path"` (no edge cases), `"all"`, or a specific edge case label
- Display names (faker-resolved MT object names) are shown as primary labels in preview and execution views; refs are available on hover

### Fund Flows UI

- **`/flows`** -- List of compiled flows with actor badges, pattern type, step/OG counts, amount ranges, Mermaid sequence diagram accordions, scenario builder with per-actor identity controls, and edge case count inputs
- **`/flows/view/<idx>`** -- Detail view with a multi-column scroll-synced T-account layout (transactions left, ledger account debits/credits right), edge case badges, trace metadata editor, and per-flow Mermaid diagram

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
| `examples/funds_flow_demo.json` | **Funds Flows DSL** starter: deposit → settle → post lifecycle with actors, ledger entries, and an optional return edge case. |
| `examples/marketplace_demo.json` | PSP marketplace: buyer/seller user frames with instance resources (LEs, CPs, wallets), ACH deposit → book fee → book settle → ACH payout, with an NSF return edge case. |
| `examples/psp_minimal.json` | Smallest useful **book** transfer between two internal accounts. Two direct actors, one step. |
| `examples/stablecoin_ramp.json` | Fiat↔stablecoin on/off-ramp: dual connections (USD + USDC), ledger accounts for reserves/positions, inline LTs on POs, and mutually exclusive payout alternatives (ACH/RTP/Wire via `exclusion_group` with `position: "replace"`). |
| `examples/staged_demo.json` | Marketplace with `staged: true` on all money-movement steps. Infrastructure creates normally; staged items get "Fire" buttons. |
| `examples/tradeify.json` | **Ledger-heavy brokerage PSP.** Rewards wallet with USDG conversion, chart-of-accounts categories, per-user instance resources (LE + CP + IA + 2 LAs + category memberships), NinjaTrader direct actor with CP + EAs, three optional groups (ACH cashout, wire funding, staged return). |

Validate examples locally:

```bash
source .venv/bin/activate
python -c "
import json; from pathlib import Path
from models import DataLoaderConfig
from engine import build_dag, dry_run
for p in sorted(Path('examples').glob('*.json')):
    config = DataLoaderConfig.model_validate(json.loads(p.read_text()))
    batches = dry_run(config, known_refs=None)
    print(f'{p.name:30s} OK  ({sum(len(b) for b in batches)} resources)')
"
```

---

## Execution flow

1. **Validate** -- Credentials check, org discovery + reconciliation, parse JSON, compile funds flows (if present), build DAG, dry run.
2. **Fund Flows** (if `funds_flows` present) -- Flow cards with actor badges, Mermaid diagrams, scenario builder for generation (per-actor identity, edge case counts, staging), metadata editor, JSON editor.
3. **Preview** -- Resources grouped by flow. Display names (faker-resolved) as primary labels, refs on hover. Edge case badges, metadata, cleanup hints. Filter/sort/search/export.
4. **Execute** -- Topological order, SSE updates, idempotency keys on creates. Duplicate category memberships handled gracefully. Staged resources resolved but held back.
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
main.py              FastAPI app factory, session init, SSE stream
engine.py            Ref resolution, DAG (graphlib), execute loop, run manifests
handlers.py          MT SDK calls, polling, retry logic, metadata stripping
helpers.py           Shared rendering: build_preview, extract_display_name, format helpers
session.py           SessionState dataclass (per-session state)
seed_loader.py       Faker hybrid seed engine (standard, industry, pop-culture)
flow_validator.py    Config-level flow validation
flow_views.py        Ledger + payments view data computation
webhooks.py          Webhook receiver, run detail, staged fire, listener

models/              Pydantic config schemas
  config.py          DataLoaderConfig (root schema)
  resources.py       MT resource models (Layers 0-6)
  shared.py          RefStr, MetadataMixin, base classes
  steps.py           Typed step models (discriminated union)
  flow_dsl.py        FundsFlowConfig, ActorFrame, GenerationRecipeV1
  settings.py        AppSettings (env config)
  runtime.py         Runtime models (HandlerResult, RunManifest)

flow_compiler/       Funds Flow DSL compiler
  core.py            compile_to_plan(), compile_flows(), emit_dataloader_config()
  generation.py      Generation pipeline: recipe → N instances, edge case pre-selection
  mermaid.py         Mermaid sequenceDiagram rendering
  pipeline.py        Compilation pipeline passes
  ir.py              FlowIR intermediate representation
  diagnostics.py     Compilation diagnostics and warnings

org/                 Org discovery + reconciliation
  discovery.py       Query MT org for existing resources
  reconciliation.py  Match config refs to discovered resources
  registry.py        RefRegistry for ref → UUID mapping

routers/             FastAPI route modules
  setup.py           /api/validate, /api/revalidate
  flows.py           /flows, /flows/view, /api/flows/generate, /api/flows/metadata
  execute.py         /api/execute, SSE stream
  cleanup.py         /api/cleanup
  runs.py            /runs, run detail, staged fire
  connection.py      /api/connections

templates/           HTMX + Jinja2 UI
  partials/          Reusable fragments (mermaid, scenario_builder, resource_row, etc.)
static/              CSS
examples/            6 example configs (marketplace, stablecoin, tradeify, staged, psp, funds_flow)
prompts/             LLM prompt kit (system_prompt, decision_rubrics, ChatGPT instructions)
seeds/               Seed catalog (4 YAML files + Faker standard)

Makefile             setup, run, tunnel, validate shortcuts
runs/, logs/         Runtime (gitignored)
tests/               Pytest suite (575 tests)
```

---

## Development

```bash
source .venv/bin/activate
python -m pytest tests/ -q           # all 575 tests
python -m pytest tests/ -x -q        # stop on first failure
```

---

## Scope

**In:** Sandbox resource creation from JSON, `$ref` DAG, SSE UI, run manifests + idempotency, metadata passthrough, webhook receiver + correlation, staged resources with live-fire UI, Funds Flows DSL (compiler, Mermaid rendering, generation pipeline, scenario builder), per-actor identity seeding, edge case pre-selection, org discovery + reconciliation, compile-time preview with display names and T-account layout.

**Out:** Embedded LLM, production attach-to-arbitrary-org mode, full CLI.
