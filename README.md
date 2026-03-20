# Modern Treasury Dataloader

A JSON-configurable demo data provisioner for Modern Treasury sandbox environments. Upload a declarative config describing the resources to create (counterparties, payment orders, ledger accounts, etc.), and the loader resolves inter-resource dependencies via a typed symbolic reference system, computes execution order from the dependency graph, and creates resources through the MT Python SDK — streaming real-time progress to a web UI.

## Quick Start

**Prerequisites**: Python 3.11+, a Modern Treasury sandbox API key and org ID.

```bash
# Install dependencies
pip install -r requirements.txt

# Configure credentials (or enter them in the web UI)
cp .env.example .env
# Edit .env with your API key and org ID

# Start the server
uvicorn main:app --reload

# Open http://localhost:8000
```

The web UI walks you through: **Setup** (credentials + JSON upload) → **Preview** (execution plan with dependency order) → **Execute** (live SSE-streamed creation) → **Runs** (history + cleanup).

## Configuration

### Environment Variables

All settings use the `DATALOADER_` prefix. They can be set in a `.env` file or as environment variables.

| Variable | Default | Description |
|----------|---------|-------------|
| `DATALOADER_MT_API_KEY` | `""` | MT API key (can also be entered per-request in the UI) |
| `DATALOADER_MT_ORG_ID` | `""` | MT organization ID (can also be entered per-request in the UI) |
| `DATALOADER_BASELINE_PATH` | `baseline.yaml` | Path to static baseline definition |
| `DATALOADER_RUNS_DIR` | `runs` | Directory for run manifest JSON files |
| `DATALOADER_LOG_LEVEL` | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `DATALOADER_STAMP_LOADER_METADATA` | `false` | Add debug metadata to created resources (opt-in) |
| `DATALOADER_MAX_CONCURRENT_REQUESTS` | `5` | Max concurrent MT API calls within a batch (1-20) |

### Baseline & Discovery

On each validation request, the loader attempts **org discovery** — querying the live MT org to find existing connections, internal accounts, and ledgers. These are auto-assigned symbolic refs and registered so your config can reference them.

If discovery fails (network timeout), the loader falls back to `baseline.yaml`, a static YAML file describing expected sandbox resources. Auth errors are surfaced immediately and do not fall back.

**Updating baseline.yaml**: Edit the file when your sandbox org changes. Each entry needs a `ref`, `id` (the MT UUID), and identifying fields (e.g., `vendor_name` for connections, `name` for internal accounts).

## JSON Config Authoring

### Typed Symbolic References

Every resource in your config gets a user-defined `ref` key. Other resources reference it using the `$ref:` syntax:

```
$ref:<resource_type>.<key>                  primary resource
$ref:<resource_type>.<key>.<selector>       child/derived resource
```

Examples:

```
$ref:connection.gringotts                        discovered connection
$ref:counterparty.vendor_bob                     counterparty you're creating
$ref:counterparty.vendor_bob.account[0]          auto-created external account
$ref:internal_account.ops_checking               internal account
$ref:internal_account.ops_checking.ledger_account auto-created ledger account
$ref:ledger.main                                 ledger
$ref:incoming_payment_detail.ipd1.transaction    auto-created bank transaction
```

The engine auto-prefixes the resource type to your `ref` key — you provide `"ref": "vendor_bob"` and it registers as `counterparty.vendor_bob`.

### Resource Types

| Resource | Required Fields | Metadata | Deletable |
|----------|----------------|----------|-----------|
| Legal Entity | `legal_entity_type` | Yes | No |
| Ledger | `name` | Yes | Yes |
| Counterparty | `name` | Yes | Yes |
| Ledger Account | `name`, `ledger_id`, `normal_balance`, `currency` | Yes | Yes |
| Internal Account | `connection_id`, `name`, `party_name`, `currency` (USD/CAD) | Yes | No |
| External Account | `counterparty_id` | Yes | Yes |
| Ledger Account Category | `name`, `ledger_id`, `normal_balance`, `currency` | Yes | Yes |
| Virtual Account | `name`, `internal_account_id` | Yes | Yes |
| Expected Payment | `reconciliation_rule_variables` | Yes | Yes |
| Payment Order | `type`, `amount`, `direction`, `originating_account_id` | Yes | No |
| Incoming Payment Detail | `type`, `direction`, `amount`, `internal_account_id` | No | No |
| Ledger Transaction | `ledger_entries[]` | Yes | No (archive) |
| Return | `returnable_id` | No | No |
| Reversal | `payment_order_id`, `reason` | Yes | No |
| Category Membership | `category_id`, `ledger_account_id` | No | Remove |
| Nested Category | `parent_category_id`, `sub_category_id` | No | Remove |
| Connection (sandbox) | `entity_id` | No | No |

Resources are created in dependency order — the loader computes a DAG from your `$ref:` edges and executes in topological batches with intra-batch concurrency.

### Metadata

Metadata is **business/demo data** — ERP IDs, invoice refs, tenant identifiers, cost centers. It is passed through to Modern Treasury unchanged on all resource types that support it.

```json
{
    "ref": "vendor_bob",
    "name": "Bob's Widgets LLC",
    "metadata": {
        "erp_vendor_id": "V-00482",
        "cost_center": "ENGOPS",
        "region": "us-west-2"
    }
}
```

The loader does not stamp its own metadata by default. The preview screen shows all metadata before execution so you can verify exactly what business context will be created.

## Example Configs

### `examples/full_demo.json`

Exercises most configurable resource types (26 resources across 4 DAG batches). Includes:
- Legal entities (business + individual with full KYB/KYC), ledger, counterparties with inline accounts
- Sandbox test counterparties (success + auto-return R01)
- Ledger accounts, categories with memberships and nesting
- Internal account, external account, virtual account
- Payment orders with inline ledger transactions
- Expected payment with reconciliation rules
- Incoming payment detail, standalone ledger transaction
- Rich business metadata on all supported types

### `examples/marketplace_demo.json`

**Boats Group x Modern Treasury PSP marketplace demo** (18 resources across 5 DAG batches). Models a complete boat purchase flow on a payment service provider (PSP) architecture:

- **Onboarding**: 4 legal entities (platform, buyer, seller-dealer, NSF buyer) with full KYB/KYC
- **Sub-accounts**: 4 internal accounts functioning as user wallets (platform revenue, buyer, seller, NSF buyer)
- **Money in**: Simulated buyer deposit ($105,000 via IPD) with auto-reconciliation against an expected payment
- **On-platform settlement**: Book transfers split funds — net ($101,850) to seller wallet, 3% fee ($3,150) to platform revenue
- **Payout**: ACH credit sends seller proceeds to their external bank
- **Return handling**: NSF buyer's ACH debit auto-returns R01 via sandbox test counterparty

The config uses `$ref:` values in metadata to enforce correct money-flow ordering through the DAG (deposit → settlement → payout) without any code changes. No virtual accounts, no ledgers — internal accounts serve as wallets.

### `examples/payments_only.json`

Lightweight config (6 resources, 3 batches). Demonstrates:
- Expected payment with matching reconciliation amounts
- Payment order against a sandbox test counterparty (success)
- Incoming payment detail that triggers auto-reconciliation

## Execution Flow

1. **Validate**: Pings MT to verify credentials, runs org discovery (or baseline preflight), parses the JSON config, builds the DAG, computes batch order
2. **Preview**: Shows the execution plan grouped by phase (Setup, Business, Lifecycle, Mutations) with dependency info, metadata preview, and cleanup scope
3. **Execute**: Creates resources in topological order via SSE streaming. Each resource updates in-place: Pending → Creating → Created (with ID) or Error
4. **Runs**: Lists past executions with their manifests. Cleanup deletes what's deletable, archives ledger transactions, and skips non-deletable resources

## Cleanup

Not all Modern Treasury resources support deletion. Cleanup operates within these constraints:

| Action | Resources |
|--------|-----------|
| **Delete** | Counterparties, External Accounts, Virtual Accounts, Ledgers, Ledger Accounts, Ledger Account Categories, Expected Payments |
| **Archive** | Ledger Transactions |
| **Remove** | Category Memberships, Nested Categories |
| **Skip** | Internal Accounts, Legal Entities, Payment Orders, Returns, Reversals, Connections |

Cleanup processes resources in **reverse creation order** and streams progress via SSE. Payment Orders, Returns, Legal Entities, Internal Accounts, Reversals, and Connections persist in the sandbox after cleanup.

## Architecture

```
dataloader/
  main.py              FastAPI app, routes, SSE streaming, session management
  models.py            Pydantic schemas, AppSettings, internal types
  engine.py            RefRegistry, DAG executor (graphlib), run manifests
  handlers.py          16 async handlers with tenacity retry/polling
  baseline.py          Org discovery, baseline YAML, preflight validation
  baseline.yaml        Static "clean sandbox" definition (discovery fallback)
  templates/           Jinja2 + HTMX frontend (17 templates)
  static/style.css     CSS with status colors, type badges, animations
  examples/            Example JSON configs
  runs/                Run manifest JSON files (created at runtime)
  logs/                Structured log files (created at runtime)
```

**Key design decisions**:
- **DAG execution** via `graphlib.TopologicalSorter` — dependency order emerges from `$ref:` edges, not hardcoded phases
- **Intra-batch concurrency** via `asyncio.TaskGroup` + `Semaphore` for rate limiting
- **SSE streaming** via `sse-starlette` with `hx-swap-oob` for targeted row updates
- **Idempotency** via run manifests + SDK `idempotency_key` (`{run_id}:{typed_ref}`)
- **Wait-for-state** via `tenacity` decorators for lifecycle resources (IPD polling, pre-create conditions)

## Development

### Project structure

| Module | Responsibility |
|--------|---------------|
| `models.py` | All Pydantic schemas, shared types, internal types, app settings |
| `engine.py` | RefRegistry, typed ref resolver, DAG builder, batch executor, run manifests |
| `handlers.py` | One async function per resource type, tenacity polling, functools.partial dispatch |
| `baseline.py` | Discovery (`discover_org`), baseline YAML loading, preflight validation |
| `main.py` | FastAPI routes, SSE streaming, session cache, cleanup dispatch |

### Adding a new resource type

1. Add a `*Config` class to `models.py` (inherit `_BaseResourceConfig` + `MetadataMixin` if supported)
2. Add it to `DataLoaderConfig` in the appropriate layer
3. Add a `create_*` handler to `handlers.py`
4. Register it in `build_handler_dispatch` and `DELETABILITY`
5. Add cleanup logic in `_cleanup_one` / `_get_resource_client` if applicable

### Running tests

```bash
python test_step6_smoke.py      # Route + template smoke tests (23 checks)
```

## v1 Scope & Limitations

**In scope**: Direct API resource creation, clean-sandbox mode, graphlib DAG execution, tenacity wait policies, run manifests with idempotency keys, SSE progress streaming, HTMX server-rendered frontend, business metadata passthrough, inline ledger account creation.

**Out of scope (future)**:
- Attach-to-existing mode (resolve refs against non-canonical orgs)
- Account collection / verification flows (microdeposits, prenotes)
- Webhook receiver (the loader polls; it does not listen)
- Multi-currency internal accounts beyond USD/CAD (SDK constraint)
- CLI mode with Rich terminal output
- `payment_orders.create_async()` for async PO creation
- `transactions.create()` for direct transaction simulation
