# Modern Treasury Dataloader

Upload a JSON **DataLoaderConfig** in the browser: the app validates it, shows execution order (DAG), and creates resources in Modern Treasury's **sandbox** via the Python SDK, with live progress (SSE). Includes a **Funds Flows DSL** for defining multi-step payment lifecycles, a compile-time preview UI, Mermaid sequence diagrams, and a generation pipeline for scaling one pattern to hundreds of instances.

---

## Quick start

### Docker

**You need:** [Docker Desktop](https://www.docker.com/products/docker-desktop/) and a Modern Treasury **sandbox** API key and org ID.

**Important:** Run every `make …` command from the **project directory** — the folder that contains `Makefile`, `Dockerfile`, and `docker-compose.yml`. If you run `make` from your home directory (`~`), Make will report `No rule to make target 'docker-build'` because there is no Makefile there.

```bash
git clone https://github.com/ejtheiss/mt-dataloader   # skip if you already cloned
cd mt-dataloader                                      # required before make
make docker-build                                     # build the image (~30 s)
make docker-run                                       # start the container
```

If the repo already exists: `cd path/to/mt-dataloader` (for example `cd ~/mt-dataloader`), then run `make docker-build`.

Open **http://localhost:8000**. Stop with `make docker-stop`.

**Ngrok in Docker:** The image does **not** download the ngrok agent during `docker build` (that step often fails with HTTP 403 from ngrok’s CDN in automated/build environments). The binary is fetched **on first tunnel start** from **`/listen`** when you save an authtoken, same as a local venv. You need outbound HTTPS to `bin.ngrok.com` from the running container. If that still fails, set **`DATALOADER_NGROK_AUTO_START=false`** and run ngrok on the host (`make tunnel`), as in [Advanced: external ngrok](#advanced-external-ngrok-optional).

**Plain HTML / no CSS:** Styles load from `/static/...`. If the page looks like unstyled text, open DevTools → Network and check whether CSS requests return **404**. Common causes: (1) the app process was started with a **working directory** that is not the project root (older builds relied on cwd; current code resolves `static/` next to `main.py` — **pull latest**), (2) a **reverse proxy** serves the app under a **path prefix** (e.g. `/dataloader`) but `/static` is not forwarded to the same app, (3) an **incomplete** image or checkout missing the `static/` tree. Quick check: `curl -sI http://localhost:8000/static/css/tokens.css` should return `200`.

### Local Python

**You need:** Python 3.11+, a Modern Treasury **sandbox** API key and org ID.

```bash
cd mt-dataloader
make setup                         # creates .venv, installs deps
source .venv/bin/activate          # Windows: .venv\Scripts\activate
make run                           # starts uvicorn with auto-reload
```

Or manually: `python3 -m venv .venv && pip install -r requirements.txt && uvicorn dataloader.main:app --reload` (repo-root `main.py` still supports `uvicorn main:app` as a shim).

---

Open **http://127.0.0.1:8000**. Use the **organization switcher** at the top of the left sidebar to add one or more MT **API keys** and **org IDs**; the active profile applies to Setup validation, execution, and the Listener. **Setup** is only for JSON (upload or paste) and **Validate**. Use **Show all orgs** on **Runs** or **Listener** to list every run or webhook row regardless of active org. Profiles are stored in **browser localStorage** (not encrypted, not synced). There is no server-side user login yet. The `.env` file is optional (e.g. `DATALOADER_WEBHOOK_SECRET`).

---

## Updating

### Update Docker Desktop (or Docker Engine)

Keep your **Docker installation** current so `docker compose` builds and runs reliably.

- **Docker Desktop** (macOS / Windows): open Docker Desktop → **Settings** (gear) → **Software updates** → check for updates, or use the menu **Check for updates**. You can also reinstall from [Docker Desktop downloads](https://www.docker.com/products/docker-desktop/).
- **Linux** (Engine + Compose plugin): use your distro’s package manager or [Docker’s install docs](https://docs.docker.com/engine/install/) so both `docker` and `docker compose` stay in sync.

Verify after an upgrade:

```bash
docker version
docker compose version
```

### Update the Dataloader container (this repo)

When a new version is pushed to GitHub, refresh your **local clone** and **rebuild the image** so the running container matches `main`.

**One command** (from the repo root; runs `git pull`, rebuilds, restarts):

```bash
cd mt-dataloader
make docker-update
```

`make docker-update` runs `git pull` then `docker compose build`, then `docker compose down && docker compose up -d`. Your `runs/` and `logs/` mounts are **unchanged** — run history and UI-persisted settings under `runs/` are kept.

**Step by step** (same outcome without the combined target):

```bash
cd mt-dataloader
git pull
make docker-build                  # or: docker compose build
make docker-stop && make docker-run   # or: docker compose down && docker compose up -d
```

If a rebuild behaves oddly after upgrading Docker itself, try a clean build once: `docker compose build --no-cache`.

To see which **app** revision you are running, use the sidebar footer in the UI or `GET /api/version`.

### Local Python

```bash
cd mt-dataloader
git pull
source .venv/bin/activate
pip install -r requirements.txt               # pick up new/changed deps
make run
```

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

- **Actor frames** define participants: **`user`** frames point at **per-instance** legal entities via **`instance_resources` on that `funds_flows[]` object** (`{instance}` in refs; `{first_name}`, `{last_name}`, `{business_name}` in template fields). **`direct`** frames use a literal **`customer_name`** and **static top-level** refs in **`slots`**. Authoring for tools: **`prompts/system_prompt.md` → *User actors (mandatory JSON)*** — do not pin variable parties with a single top-level LE ref.
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
- **`instance_resources`** on each `FundsFlowConfig` defines per-copy infrastructure templates (LEs, CPs, IAs, LAs, category memberships) expanded with `{first_name}`, `{last_name}`, `{business_name}`, `{instance}`, etc. — **required JSON shape for variable `user` actors** (same placeholder set as in prompts)
- **Edge cases** activate `optional_groups` by discrete count — each group gets an exact number of instances, with mutually exclusive groups (via `exclusion_group`) distributing counts by weight
- **Staged selection** stages instances from: `"happy_path"` (no edge cases), `"all"`, or a specific edge case label
- Display names (resolved from materialized template / profile values) are shown as primary labels in preview and execution views; refs are available on hover

### Fund Flows UI

- **`/flows`** -- List of compiled flows with actor badges, pattern type, step/OG counts, amount ranges, Mermaid sequence diagram accordions, scenario builder with per-actor identity controls, and edge case count inputs
- **`/flows/view/<idx>`** -- Detail view with a multi-column scroll-synced T-account layout (transactions left, ledger account debits/credits right), edge case badges, trace metadata editor, and per-flow Mermaid diagram

### Mermaid diagrams

Each compiled flow generates a Mermaid `sequenceDiagram` showing actors, message arrows by payment type, and `opt` blocks for optional groups. Diagrams render client-side via Mermaid.js and can be copied as syntax or SVG.

---

## JSON config

- **Schema (for LLMs / tools):** `GET /api/schema` -- full `DataLoaderConfig` JSON Schema.
- **Validate without UI:** `POST /api/validate-json` -- body = raw JSON; returns **JSON API v1** (`schema_version`, `ok`, `phase`, `errors[]` with `code`/`message`/`path`) for repair loops.
- **Funds flows (`user_N`):** for any participant that should not be the same party on every copy of a flow, put **`instance_resources`** on **that `funds_flows[]` entry**, use **`{instance}`** in `ref` keys and in **`user_N` `entity_ref` / slot `$ref`s**, and use name placeholders per **`prompts/system_prompt.md` → *User actors (mandatory JSON)***. Do not wire variable parties from a single top-level legal entity unless the story explicitly requires one fixed actor.

Resources reference each other with **`$ref:<resource_type>.<ref>`** (e.g. `$ref:internal_account.buyer_maya_wallet`). The `ref` field on each object is a short key; the engine builds the typed name. Child refs include selectors like `$ref:counterparty.vendor_cp.account[0]`.

**Legal entities (sandbox):** For demos, you only need `ref`, `legal_entity_type`, and name fields in JSON. The app **replaces** identifications, addresses, documents, and related compliance fields with deterministic mock data before calling MT, so sandbox KYC/KYB stays predictable.

**Connections (sandbox):** **`entity_id: "modern_treasury"`** is the default **PSP sandbox** connection. It supports **ACH**, **wire**, **RTP**, **book**, **USD**, and **stablecoins** (e.g. **USDC**, **USDG**, **PYUSD**, **USDT**—confirm the exact set in current MT sandbox docs). Use **one** `connections[]` row and reference it from internal accounts via **`$ref:connection.<ref>`**; currency and payment rail come from the **internal account** and **payment / IPD** fields, not from extra PSP connections. Reserve **`example1`** and **`example2`** for **BYOB** (bring-your-own-bank) simulation stories (Gringotts / Iron Bank–style behaviors per MT docs)—**not** because the PSP sandbox lacks ACH or wire. See `prompts/decision_rubrics.md` (Connections).

After creating a legal entity, the engine **polls** until MT reports `active` (or timeout) before continuing, so dependent internal accounts are less likely to race pending compliance.

See **`prompts/`** -- start with **`prompts/README.md`** (what each file is for) and **`prompts/system_prompt.md`** (output format + paste order). Use the files under **`examples/`** as structural templates.

---

## Webhooks (optional)

Receive real-time MT webhook events correlated to dataloader runs. The dataloader manages an ngrok tunnel from within the app — no separate terminal needed.

### Setup (2 minutes, one-time)

1. Create a **free account** at [ngrok.com/signup](https://ngrok.com/signup) and copy your **authtoken** from the [dashboard](https://dashboard.ngrok.com/get-started/your-authtoken)
2. Start the dataloader: `make docker-run` (or `make run`)
3. Open **http://localhost:8000/listen**
4. Paste your authtoken and click **Start Tunnel**
5. Click **Register in MT** — the app auto-creates a webhook endpoint in MT and captures the signing secret

> **Tip:** Free ngrok accounts include one **stable static domain** (`*.ngrok-free.app`). Claim yours at [dashboard.ngrok.com/domains](https://dashboard.ngrok.com/domains) and enter it in the domain field. With a static domain, the webhook URL never changes — registration is truly one-time.

### Subsequent sessions

If you entered your authtoken previously, the tunnel **auto-starts** when the container starts. Open `/listen` to verify the green status dot. With a static domain, MT's webhook endpoint still points to the same URL — zero manual steps.

### Verify it works

Click **Send Test Webhook** on the `/listen` page — a synthetic event should appear in the live feed. Then run a config and watch real MT events stream in.

### Ngrok: “too many agent sessions” (ERR_NGROK_108)

Free ngrok allows **three concurrent agent sessions** per account. Each separate ngrok process counts (other terminals, Docker replicas, IDE tunnels, or stale sessions). Open [dashboard.ngrok.com/agents](https://dashboard.ngrok.com/agents) to disconnect idle agents, stop extra `ngrok` processes, or upgrade. If you already run ngrok yourself and only want the dataloader to **attach** to it, set **`DATALOADER_NGROK_AUTO_START=false`** so the app does not spawn another agent on startup.

On **`/listen`**, tunnel start failures (including ERR_NGROK_108) are shown in the **amber health banner** under the tunnel panel. **Optional:** set **`DATALOADER_NGROK_API_KEY`** to a [ngrok API key](https://dashboard.ngrok.com/api-keys) (Bearer token for `api.ngrok.com`, not the agent authtoken). Then the banner can **list remote agent sessions** and **stop** them from the UI without opening the dashboard.

### Advanced: external ngrok (optional)

If you prefer running ngrok outside the app (e.g. in a separate terminal), that still works:

```bash
# Terminal 1 — start the app
make run

# Terminal 2 — start ngrok
make tunnel
```

The `/listen` page auto-detects external tunnels via ngrok's local API (`127.0.0.1:4040`). Copy the URL and create a webhook endpoint manually in **MT Dashboard → Developers → Webhooks → Add Endpoint** with URL `https://your-tunnel.ngrok-free.app/webhooks/mt`.

### Signature verification

When using **Register in MT**, the signing secret is captured automatically. For manual setup, add it to `.env`:

```bash
DATALOADER_WEBHOOK_SECRET=whsec_...
```

Without it the receiver accepts all payloads — fine for sandbox demos.

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
| `examples/stablecoin_ramp.json` | Fiat↔stablecoin on/off-ramp: one `modern_treasury` connection, USD + USDC internal accounts, IPD/PO steps only (no ledger), and mutually exclusive payout alternatives (ACH/RTP/Wire via `exclusion_group` with `position: "replace"`). |
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
3. **Preview** -- Resources grouped by flow. Display names (resolved labels) as primary labels, refs on hover. Edge case badges, metadata, cleanup hints. Filter/sort/search/export.
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
main.py              ASGI shim → re-exports dataloader.main.app (uvicorn dataloader.main:app preferred)
dataloader/          Application package (02a Phase E — see maintainer plan 00 / 02a)
  main.py            FastAPI app, lifespan, router includes, static/templates paths
  _version.py        App version (sidebar / GET /api/version)
  helpers.py         Shared rendering: build_preview, extract_display_name, format helpers
  tunnel.py          Ngrok tunnel manager used by /listen
  sse_helpers.py     SSE stream helpers
  ngrok_cloud.py     Optional ngrok Cloud API (remote agents)
  mt_doc_links.py    MT dashboard deep links
  mt_webhook_endpoints.py  Webhook endpoint registration helpers
  routers/           FastAPI route modules (import: dataloader.routers)
    setup/           Setup package; __init__.py wires one APIRouter (pages, json_api, htmx_validate,
                     drafts, resource_partials, _helpers)
    flows.py         /flows, /flows/view, /api/flows/generate, /api/flows/metadata
    execute.py       /api/execute, SSE stream
    cleanup.py       /api/cleanup
    runs.py          /runs, run detail, staged fire
    connection.py    /api/connections
    tunnel.py        /listen tunnel UI
    deps.py          FastAPI Depends helpers
  webhooks/          Webhook package (routes.py + correlation helpers)
  staged_fire.py     FIREABLE_TYPES shared by webhooks + engine dry-run (must match _FIRE_DISPATCH)
  engine/            DAG executor (submodules: refs, dag, runner, run_meta)
  handlers/          MT SDK handlers (submodules: constants, operations, dispatch)
  session/           SessionState + in-memory sessions; draft_persist (Plan 0 Wave D)
flow_compiler/       Funds Flow DSL compiler (no imports from dataloader — plan 00)
  flow_validator.py  Config-level flow validation
  seed_loader.py     Faker hybrid seed engine (standard, industry, pop-culture)
  seeds/             Seed catalogs (YAML)
  flow_views.py      Ledger + payments view data for Fund Flows UI
```

### Application wiring

- **ASGI:** `uvicorn dataloader.main:app` (root `main.py` may re-export `app`).
- **SQLite (Plan 0):** `DATALOADER_DATA_DIR` (default `data/`) holds `dataloader.sqlite`; lifespan runs `alembic upgrade head` then opens an async SQLAlchemy engine. CI runs `alembic upgrade head` before `pytest`.
- **Factory + lifespan:** `dataloader/main.py` — settings, logging, static, templates, router includes, tunnel manager.
- **HTTP:** `dataloader/routers/`; **webhooks:** `dataloader/webhooks/`.
- **DAG + SDK:** `dataloader/engine/`, `dataloader/handlers/`; **staged fire allowlist:** `dataloader/staged_fire.py` (`FIREABLE_TYPES`).
- **Loader session:** `dataloader/session/` — `SessionState` and the in-memory `sessions` map (**cache**). **Plan 0 Wave D:** SQLite `loader_drafts` (see `db/repositories/loader_drafts.py`, `models/loader_draft.py`, `dataloader/session/draft_persist.py`) is the continuity store; execute does not delete the draft row. Single-worker; see maintainer **Plan 0** for follow-ups.
- **Injection:** `dataloader/routers/deps.py` — settings, templates, tunnel, session lookup helpers.
- **Import boundaries:** run `lint-imports` (config: `pyproject.toml` → `[tool.importlinter]`). `flow_compiler` and `models` must not import `dataloader`. **`org`** is forbidden from `dataloader.routers`, `webhooks`, `handlers`, `session`, `main` — in practice **`org` imports `dataloader.engine` only** (matches contracts).

```
db/                  SQLAlchemy ORM + Alembic (`tables`, `database`, `repositories/*`) — no imports from `dataloader`
                     (e.g. `runs`, `webhooks`, `loader_drafts`)
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
  flow_validator.py  Config-level flow validation (advisory diagnostics)
  seed_loader.py     Dataset profiles + YAML seeds under seeds/
  mermaid.py         Mermaid sequenceDiagram rendering
  pipeline.py        Compilation pipeline passes
  ir.py              FlowIR intermediate representation
  diagnostics.py     Compilation diagnostics and warnings
  flow_views.py      View rows/columns for T-account / payments UI

org/                 Org discovery + reconciliation
  discovery.py       Query MT org for existing resources
  reconciliation.py  Match config refs to discovered resources
  registry.py        RefRegistry for ref → UUID mapping

templates/           HTMX + Jinja2 UI
  partials/          Reusable fragments (mermaid, scenario_builder, resource_row, etc.)
static/              CSS
examples/            6 example configs (marketplace, stablecoin, tradeify, staged, psp, funds_flow)
prompts/             LLM prompt kit (system_prompt, decision_rubrics, ChatGPT instructions)
flow_compiler/seeds/ Seed catalog (YAML + Faker standard; used by flow_compiler.seed_loader)

Makefile             setup, run, tunnel, validate shortcuts
runs/, logs/         Runtime (gitignored)
tests/               Pytest suite (718 tests)
```

A local **`plan/`** directory (roadmaps, design notes) is **gitignored** and is not part of the published repository.

---

## Development

See **[`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md)** for venv setup, tests, and running the app.

```bash
source .venv/bin/activate
python -m pytest tests/ -q           # full suite
python -m pytest tests/ -x -q      # stop on first failure
```

**MINT / MT UI layout:** [`docs/PORTING-KIT.md`](docs/PORTING-KIT.md), [`docs/DESIGN_SYSTEM_AUTHORITY.md`](docs/DESIGN_SYSTEM_AUTHORITY.md), [`docs/RESOURCES.md`](docs/RESOURCES.md) (optional token regen). **Naming / preview / display pipelines:** [`docs/ARCHITECTURE_NAMING_AND_DISPLAY.md`](docs/ARCHITECTURE_NAMING_AND_DISPLAY.md).

---

## Scope

**In:** Sandbox resource creation from JSON, `$ref` DAG, SSE UI, run manifests + idempotency, metadata passthrough, webhook receiver + correlation, staged resources with live-fire UI, Funds Flows DSL (compiler, Mermaid rendering, generation pipeline, scenario builder), per-actor identity seeding, edge case pre-selection, org discovery + reconciliation, compile-time preview with display names and T-account layout.

**Out:** Embedded LLM, production attach-to-arbitrary-org mode, full CLI.
