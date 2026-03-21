# Webhook Listener + Run Detail + Staged Resources — Project Plan v3

## Overview

Add five capabilities to the dataloader:
1. **Persist submitted config** per run (currently only a hash)
2. **Inbound webhook endpoint** that receives MT webhook payloads, correlates them to runs, and stores them
3. **Run detail page** with config viewer, resource list, and live/historical webhook feed
4. **Standalone listener page** (no run required) for general webhook monitoring
5. **Staged resources** — payment orders, incoming payment details, expected payments, and ledger transactions marked `staged: true` are resolved but held back during execution, then fired manually from the run detail page via button clicks during a live demo

**Optional by design**: webhooks require a tunnel (ngrok) and MT dashboard configuration. Orgs without a webhook endpoint skip it entirely — the rest of the app works unchanged. The listener page detects tunnel availability and guides the user.

All changes follow the existing patterns: HTMX + SSE for live updates, Jinja2 partials for rendering, file-based persistence in `runs/`.

---

## Step 1 — Persistence, Webhook Receiver & SSE Stream (Backend) ✅ COMPLETED

Config snapshot, webhook data structures, inbound endpoint, correlation index,
inline index hook in engine, and SSE fan-out stream. All backend; testable via
curl before any UI work.

**Detailed sub-plan:** `plan/step1w_persistence_and_webhooks.md`
**Commit:** `72b2c0d` — 5 files, 330 insertions

### 1a. Config snapshot at run start

Write `runs/<run_id>_config.json` inside `execute_stream()` in `main.py`,
immediately after `run_id = generate_run_id()` (currently line 641).

**Note:** `session.config_json_text` is the **pretty-printed** version (via
`json.dumps(json.loads(raw_json), indent=2)` at main.py line 449). The
persisted file is this formatted version, not the raw upload. This does NOT
match `config_hash()` (which uses `model_dump_json(exclude_none=True)`) — the
hash is a canonical serialization for idempotency, not a display format.

### 1b. Webhook JSONL per run + unmatched log

`runs/<run_id>_webhooks.jsonl` — one JSON object per line, append-only.
`runs/_webhooks_unmatched.jsonl` — catch-all for webhooks that don't correlate.

### 1c. WebhookEntry dataclass + ring buffer + bounded dedup set

In-memory `deque(maxlen=500)` for recent entries. Includes `webhook_id` (from
`X-Webhook-ID` header) for deduplication — MT retries webhooks with the same
ID. Deduplication uses a bounded `deque(maxlen=2000)` backing a `set` for O(1)
lookup with automatic eviction of old IDs. This prevents unbounded memory
growth from the dedup tracker (MT's retry window is hours, so 2000 IDs covers
the full retry window at sandbox rate of 300/min).

### 1d. Webhook receiver: `POST /webhooks/mt`

Parses the actual MT webhook payload structure:
- `event` field in body = verb (`"created"`, `"updated"`, `"completed"`, etc.)
- `X-Topic` header = resource type (`"payment_order"`, `"legal_entity"`, etc.)
- `data.id` in body = resource UUID
- `data.object` in body = resource type string (redundant with topic)
- Constructed `event_type` = `f"{topic}.{event}"` (e.g. `"payment_order.completed"`)

**Critical**: Read raw body once (`await request.body()`), verify signature
against raw bytes if configured, then `json.loads()`. Do NOT call
`request.json()` separately — Starlette may consume the stream.

**Error responses use `JSONResponse`** — NOT `return {...}, 401` (that's Flask
syntax; FastAPI ignores the tuple and returns 200). Signature failures return
`JSONResponse(status_code=401)`. Malformed payloads (invalid JSON, missing
fields) return `JSONResponse(status_code=400)` so MT does not retry them (MT
only retries on 5XX and timeouts).

### 1e. Signature verification via MT SDK

`client.webhooks.validate_signature(payload, headers)` — sync method, no
network call, no `async with` context manager needed. Cache a **module-level
singleton** `AsyncModernTreasury` client (constructed lazily on first webhook)
to avoid allocating a new SDK client per request. The client is constructed
with `webhook_key=settings.webhook_secret` and dummy api_key/org_id (signature
verification doesn't use them).

### 1f. Correlation index

Flat `dict[str, tuple[str, str]]` mapping `created_id → (run_id, typed_ref)`.
O(1) lookup instead of nested dict scan.

### 1g. Inline index hook in engine

New optional callback `on_resource_created` on `execute()` — called inside
`create_one()` immediately after `registry.register()`. Preserves engine.py's
"zero SDK dependency" principle. Wired in `main.py` to call the webhook
module's `index_resource()`.

### 1h. SSE fan-out: `GET /webhooks/stream`

Independent SSE endpoint with optional `?run_id=` filter and `?no_replay=true`
to skip ring buffer replay (used by run detail page where historical data is
already server-rendered from JSONL). Each connected listener gets an
`asyncio.Queue`. Fan-out on webhook receipt.

### 1i. Module structure: `webhooks.py`

New file using FastAPI `APIRouter`. All webhook/run-detail/staged logic lives
here. `main.py` does `app.include_router(webhook_router)`.

---

## Step 2 — Staged Resources (Model + Engine) ✅ COMPLETED

`staged` field on four config types, engine skip logic, manifest changes,
and resolved payload persistence.

**Detailed sub-plan:** `plan/step2w_staged_payment_orders.md`
**Commit:** `7c8c1cc` (partial: staged field + type annotation fix) + follow-up (StagedEntry, RunManifest, engine skip, validator, persistence)

### 2a. `staged` field — four types

```python
staged: bool = Field(default=False, exclude=True)
```

Added to: `PaymentOrderConfig`, `IncomingPaymentDetailConfig`,
`ExpectedPaymentConfig`, `LedgerTransactionConfig`.

`exclude=True` is **mandatory** — without it, `model_dump()` includes
`"staged": false` in the resolved dict passed to the SDK create method,
which fails because the SDK doesn't accept a `staged` kwarg. Mirrors
`depends_on`'s `Field(exclude=True)`.

### 2b. Engine skip in `create_one()`

After `resolved = resolve_refs(resource, registry)`, check
`getattr(resource, 'staged', False)`. If staged: save resolved payload to
`staged_payloads` dict, record in manifest, **write manifest to disk**
(crash recovery), emit SSE "staged" event, return early. Do NOT call the
handler or register in the registry.

This works for all four types with zero dispatch — `getattr` returns `False`
for types without a `staged` field.

**`manifest.write()` is mandatory** (review fix #3): Normal resources call
`manifest.write(runs_dir)` after every `manifest.record()`. Without the
same pattern for staged resources, a crash after staging would lose staged
entries from the on-disk manifest.

### 2c. DAG interaction + two-pass validator

Staged resources stay in the DAG so dependencies create first. After the
staged skip, `ts.done(*to_create)` still runs for the whole batch (line 506),
correctly unblocking downstream. But: no `created_id` is registered, so
`resolve_refs()` fails with `KeyError` for any `$ref:` targeting a staged
resource.

**Mandatory two-pass dry-run validator** (review fixes #1 and #2):

- **Pass 1 (staged resources)**: Check data-field `$ref:` only (not
  `depends_on`). Reject if any data-field ref points to another staged
  resource or a child of one. `depends_on` between staged resources is
  fine (ordering only, no registry lookup).
- **Pass 2 (non-staged resources)**: Check ALL deps (data fields +
  `depends_on`). Reject if any dep points to a staged resource or a child
  of one.

Both passes use `_dep_hits_staged()` which checks direct match AND
parent-ref match (child refs like `type.key.child` where `type.key` is
staged). This mirrors `_is_known_or_child()` already in `dry_run()`.

### 2d. Manifest: `StagedEntry` + `resources_staged`

New dataclass and list on `RunManifest`. Serialized to manifest JSON.
`RunManifest.load()` must deserialize it. `StagedEntry` includes
`resource_type` for fire dispatch.

### 2e. Fire dispatch (Step 3 concern, noted here)

The fire endpoint needs a dispatch table by `resource_type`:
- `payment_order` → `client.payment_orders.create(**payload)`
- `expected_payment` → `client.expected_payments.create(**payload)`
- `ledger_transaction` → `client.ledger_transactions.create(**payload)`
- `incoming_payment_detail` → `client.incoming_payment_details.create_async(**payload)` + **poll** via `_poll_ipd_status` + harvest child refs (`transaction_id`, `ledger_transaction_id`)

IPDs use polling (not webhooks) for completion status.

### 2f. Staged payload persistence

`staged_payloads` dict written inside `execute()` as
`runs/<run_id>_staged.json` at run completion (same pattern as
`manifest.write()`). Contains fully-resolved payloads with UUIDs, ready for
direct SDK calls.

> **Note:** `AppSettings.webhook_secret` is a Step 1 dependency (needed by the
> webhook receiver) and is implemented in Step 1 task 1.2, not here.

---

## Step 2b — LLM Prompt Rewrite (Staged Resources + Schema)

Rewrite the `prompts/` kit so the LLM knows about `staged: true`, the DAG
constraint (non-staged cannot depend on staged), `webhook_secret` on
`AppSettings`, and how to generate configs that use staging for demo
narratives. Webhooks are purely a UI/backend concern and don't affect config
generation — the prompts focus almost entirely on staged resources.

### Why a dedicated step

The LLM prompt kit is the primary interface for config generation. If the
prompts don't explain staged resources, the LLM will never produce them
(or worse, will produce invalid configs with non-staged resources depending
on staged ones). This step should land immediately after Step 2 (model +
engine) so that the schema and prompts are in sync before anyone tries
generating staged configs.

### 2b-1. `system_prompt.md` — generation rules + validation loop

**New generation rule** (after current rule 16):

```markdown
17. **Staged resources (demo-only)** — Four resource types support
    `staged: true`: `payment_orders`, `incoming_payment_details`,
    `expected_payments`, `ledger_transactions`. A staged resource is
    **resolved** (all `$ref:` strings replaced with real IDs) but **not
    created** via the API during execution. Instead, it appears in the run
    detail page with a "Fire" button for manual triggering during a live
    demo.

    - **Default is `false`** — omit `staged` entirely for normal execution.
      Only add `staged: true` when the user wants a "click to fire" demo
      narrative.
    - **Constraint:** A non-staged resource **cannot** depend on a staged
      resource (by `$ref:` in a field or by `depends_on`). The validator
      catches this. Either un-stage the dependency or also stage the
      dependent resource.
    - **Staged → staged is allowed:** Two staged resources can depend on
      each other. Fire ordering is handled in the UI.
    - **Staged → non-staged is normal:** The non-staged resource creates
      first (DAG ordering), then the staged resource resolves its refs and
      is held back.
    - **Typical pattern:** Stage the "exciting" business resources (the
      payment, the deposit, the journal entry) while infrastructure
      (connections, LEs, IAs, CPs) executes automatically.
```

**Update validation loop** — add the staged dep error to the "Common fixes"
list:

```markdown
- `value_error` on `(dag)` mentioning "depends on staged resource" — the
  config has a non-staged resource referencing a staged resource. Either
  remove `staged: true` from the dependency, or add `staged: true` to the
  dependent resource as well.
```

**Update workflow** — step 1 ("Understand the demo") should mention:

```markdown
   - **Demo pacing?** Should some resources fire live during the demo
     (`staged: true`) or should everything run at once?
```

### 2b-2. `decision_rubrics.md` — per-type staged notes

Add a `staged` row/note to each of the four relevant sections:

**Payment Orders** section (after the "Rules" list):

```markdown
### Staged payment orders

Add `"staged": true` to hold a PO for manual firing during a live demo.
The resolved payload (with real UUIDs) is saved; clicking "Fire" in the
run detail page sends it to the API.

Good candidates for staging: the "big payment" in a marketplace flow
(settlement to seller, payout to bank), or an ACH collection that
triggers an NSF return.

**Do not stage** a PO that other non-staged resources depend on (e.g.
a reversal targeting it). Stage both or neither.
```

**Incoming Payment Details** section (after the "Downstream book transfers"
paragraph):

```markdown
### Staged incoming payment details

Add `"staged": true` to hold an IPD for manual firing. When fired, the
fire endpoint calls `create_async()` and **polls** for completion (same
as the normal handler). After completion, child refs (`transaction_id`,
`ledger_transaction_id`) are harvested and registered.

Good candidates: the "buyer deposits funds" moment in a marketplace demo.

If downstream book transfers `depends_on` the IPD and are also staged,
the UI enforces fire ordering (fire IPD first, then POs).
```

**Expected Payments** section (after the recon notes):

```markdown
### Staged expected payments

Add `"staged": true` to hold an EP for manual firing during a recon demo.
Useful for demos where you first create the EP live, then trigger the
IPD to show the match happening.

Nothing typically depends on an EP, so staging is always safe.
```

**Ledger Transactions** section (after the "standalone ledger transaction"
row):

```markdown
### Staged ledger transactions

Add `"staged": true` to hold an LT for manual firing. Useful for demos
where journal entries are posted live ("now let's record the revenue
recognition").

**Watch for `ledgerable_id` refs:** If an LT has
`"ledgerable_id": "$ref:payment_order.po_x"` and `po_x` is staged, the
LT **cannot** be non-staged (validator catches this). Either stage both
or un-stage the PO.
```

**New cross-cutting section** (after Reversals, before Cleanup):

```markdown
## Staged Resources (Cross-Cutting)

Four resource types support `staged: true`: payment orders, incoming
payment details, expected payments, and ledger transactions.

| Staging... | Result |
|-----------|--------|
| Infrastructure (connections, LEs, IAs, CPs) | **Not supported** — these are needed as dependencies |
| Business actions (POs, IPDs, EPs, LTs) | **Supported** — resolved but held for manual fire |

### When to suggest staging

- User says "demo", "live", "step-by-step", "click to fire", "show each
  payment one at a time"
- User wants to narrate a flow: "first the deposit lands, then we settle"
- User wants audience interaction: "I'll fire the payment when the
  audience is ready"

### When NOT to suggest staging

- User wants everything to run automatically (default behavior)
- Batch/load-test configs — staging defeats the purpose
- User didn't mention demo pacing or live interaction

### Constraint reminder

Non-staged resources **cannot** depend on staged resources (`$ref:` or
`depends_on`). The dry-run validator catches this with a clear error.
Fix: stage both, or un-stage the dependency.
```

### 2b-3. `generation_profiles.md` — scope ladder

Add `staged` to the scope ladder table:

```markdown
| Staged resources (`staged: true`) | No (unless demo pacing needed) | Yes (if live demo narrative) |
```

Add to the "Quick mapping" table:

```markdown
| "Step-by-step demo", "fire each payment", "live clicks" | B or C + staged | `marketplace_demo.json` + `staged: true` on POs/IPDs |
```

### 2b-4. `ordering_rules.md` — staged DAG interaction

Add a new section after "Common Patterns Requiring `depends_on`":

```markdown
## Staged Resources and DAG Ordering

Resources with `staged: true` participate in the DAG normally — their
dependencies are resolved and their refs are evaluated. But the API call
is skipped; the resolved payload is saved for manual firing from the UI.

### Rules

1. **Non-staged cannot depend on staged** — a non-staged resource with a
   `$ref:` or `depends_on` pointing to a staged resource will fail
   validation. The staged resource has no `created_id`, so the ref can't
   resolve at execution time.

2. **Staged can depend on non-staged** — normal. The non-staged resource
   creates first, then the staged resource resolves its refs (getting real
   UUIDs) and is held back.

3. **Staged can depend on staged** — allowed. Both are resolved against
   already-created infrastructure. Fire ordering is a UI concern.

### Example: staged marketplace flow

```json
{
  "incoming_payment_details": [
    {
      "ref": "ipd_buyer_deposit",
      "type": "ach",
      "direction": "credit",
      "amount": 13750000,
      "internal_account_id": "$ref:internal_account.buyer_wallet",
      "staged": true
    }
  ],
  "payment_orders": [
    {
      "ref": "po_settle_to_seller",
      "type": "book",
      "direction": "credit",
      "amount": 13337500,
      "originating_account_id": "$ref:internal_account.buyer_wallet",
      "receiving_account_id": "$ref:internal_account.seller_wallet",
      "depends_on": ["$ref:incoming_payment_detail.ipd_buyer_deposit"],
      "staged": true
    }
  ]
}
```

Both are staged, both depend on non-staged internal accounts (which
create normally). The demo presenter fires the IPD first, then the PO.
```

### 2b-5. `naming_conventions.md` — no changes

`staged` is a boolean field, not a ref. No naming convention changes needed.

### 2b-6. `metadata_patterns.md` — no changes

`staged` is orthogonal to metadata. No patterns to add.

### 2b-7. `README.md` — mention staged support

Add a note to the table:

```markdown
| **`system_prompt.md`** | ... generation rules, validation loop. **Includes `staged: true` docs for demo-pacing.** |
| **`decision_rubrics.md`** | ... **Per-type staging notes + cross-cutting "Staged Resources" section.** |
```

### File-Level Summary

| File | Changes |
|------|---------|
| `prompts/system_prompt.md` | New generation rule 17 (staged), validation error, workflow question | 
| `prompts/decision_rubrics.md` | 4 per-type staged subsections + 1 cross-cutting "Staged Resources" section |
| `prompts/generation_profiles.md` | Scope ladder row + quick mapping row |
| `prompts/ordering_rules.md` | New "Staged Resources and DAG Ordering" section with rules + example |
| `prompts/naming_conventions.md` | No changes |
| `prompts/metadata_patterns.md` | No changes |
| `prompts/README.md` | Table update |

**Total: ~120 new lines across 5 files.**

---

## Step 3 — UI: Run Detail, Listener, Staged Fire, Navigation

Templates, routes, CSS, and wiring.

**Detailed sub-plan:** `plan/step3w_ui_run_detail_listener_fire.md`

### 3a. Run detail page: `GET /runs/{run_id}`

Four-tab layout (CSS show/hide, NOT lazy-load — keeps SSE alive):
- **Config** tab: `<pre>` with read-only JSON
- **Resources** tab: phase-grouped table from manifest (needs `DisplayPhase`
  lookup by `resource_type` — add a `RESOURCE_TYPE_TO_PHASE` map)
- **Staged** tab: fire buttons for each staged PO
- **Webhooks** tab: historical list + live SSE via
  `/webhooks/stream?run_id=X&no_replay=true`

**Dedup between JSONL history and SSE**: The Webhooks tab renders historical
entries from JSONL server-side on page load, then connects SSE for live-only
updates. The SSE stream accepts `?no_replay=true` to skip the ring buffer
replay (since historical data is already rendered). This avoids the duplicate
problem where the same webhook appears both in the server-rendered list and
in the SSE replay. The standalone listener page (`/listen`) does NOT pass
`no_replay` — it uses SSE replay for initial population.

### 3b. Fire endpoint: `POST /api/runs/{run_id}/fire/{typed_ref:path}`

Loads resolved payload from `_staged.json`, dispatches to the correct SDK
method based on `resource_type` prefix of `typed_ref`:

| `resource_type` | SDK call | Post-fire |
|----------------|----------|-----------|
| `payment_order` | `client.payment_orders.create(**resolved)` | Index `created_id` |
| `expected_payment` | `client.expected_payments.create(**resolved)` | Index `created_id` |
| `ledger_transaction` | `client.ledger_transactions.create(**resolved)` | Index `created_id` |
| `incoming_payment_detail` | `client.incoming_payment_details.create_async(**resolved)` → **poll** via `_poll_ipd_status` | Index `created_id` + harvest & register child refs (`transaction_id`, `ledger_transaction_id`) |

IPDs use polling (not webhooks) to detect completion and harvest child refs.

Updates manifest (append to `resources_created`, remove from
`resources_staged`), indexes new `created_id` for webhook correlation.
Returns HTMX partial.

Idempotency key: `{run_id}:staged:{typed_ref}`.
Credentials: from form fields (same pattern as cleanup).

### 3c. Standalone listener: `GET /listen`

Webhook URL display, tunnel auto-detection (ngrok API probe), test webhook
button, live SSE feed.

### 3d. Navigation updates

- `/listen` tab in `base.html` (NOT just `runs_page.html`) with
  `{% block nav_listen %}{% endblock %}`
- "Details" link on each run card in `runs.html`
- Direct link to `/runs/{run_id}` in `run_complete.html`

### 3e. `resource_row.html`: staged status

New `{% elif status == "staged" %}` block — gray/muted with pause icon.

### 3f. CSS

Webhook rows, staged rows, tab strip, fire button, copy button, tunnel
status banner.

---

## File Changes Summary

| File | Change | Lines (est.) |
|------|--------|-------------|
| **models.py** | `staged: bool = Field(default=False, exclude=True)` on 4 config types, `StagedEntry` dataclass, `webhook_secret` on `AppSettings` | +18 |
| **engine.py** | `on_resource_created` callback param on `execute()`, staged skip in `create_one()`, `record_staged()` on `RunManifest`, `resources_staged` field, `_staged.json` write, `_to_dict()`/`load()` for staged entries | +55 |
| **handlers.py** | No changes | 0 |
| **main.py** | Config persistence (write `_config.json`), include webhook router, wire `on_resource_created` callback | +20 |
| **webhooks.py** | New — `APIRouter`, webhook receiver, SSE stream, run detail route, listen route, fire endpoint, correlation index, fan-out logic, tunnel detection, `WebhookEntry`, ring buffer | +250 |
| **prompts/system_prompt.md** | Generation rule 17 (staged), staged dep validation error, workflow question | +35 |
| **prompts/decision_rubrics.md** | 4 per-type staged subsections + "Staged Resources" cross-cutting section | +55 |
| **prompts/generation_profiles.md** | Scope ladder row + quick mapping row for staged | +5 |
| **prompts/ordering_rules.md** | "Staged Resources and DAG Ordering" section with rules + JSON example | +30 |
| **prompts/README.md** | Table annotations for staged docs | +2 |
| **templates/run_detail.html** | New — four-tab run detail page | +150 |
| **templates/listen.html** | New — standalone listener page with tunnel banner | +60 |
| **templates/partials/webhook_row.html** | New — single webhook entry partial | +15 |
| **templates/partials/staged_row.html** | New — staged resource card with fire button | +25 |
| **templates/partials/resource_row.html** | Add "staged" status rendering | +8 |
| **templates/partials/run_complete.html** | Add link to `/runs/{run_id}` | +2 |
| **templates/runs.html** | Add "Details" link per run card | +3 |
| **templates/base.html** | Add `/listen` nav tab | +2 |
| **static/style.css** | Webhook rows, staged rows, tabs, fire button, copy button, tunnel banner | +60 |
| **requirements.txt** | Pin `httpx` explicitly (currently transitive only) | +1 |

**Total: ~800 new lines across 20 files.**

---

## Implementation Order

### Step 1 — Backend: Persistence + Webhooks (~240 lines, 4-5 tasks)
1. Config persistence in `main.py` (10 lines)
2. `AppSettings.webhook_secret` in `models.py` (3 lines)
3. `webhooks.py` scaffold: `WebhookEntry`, bounded dedup set, ring buffer, correlation index, cached sig client, `APIRouter` (50 lines)
4. Webhook receiver `POST /webhooks/mt` with `JSONResponse` errors, malformed payload guard, SDK signature verification (45 lines)
5. `on_resource_created` callback in `engine.py` + wiring in `main.py` (15 lines)
6. Webhook SSE stream `GET /webhooks/stream` with `no_replay` param (45 lines)
7. Router mount + imports in `main.py` (5 lines)

### Step 2 — Backend: Staged Resources (~85 lines, 3-4 tasks)
7. `staged` field on 4 config types + `StagedEntry` in `models.py` (15 lines)
8. Staged skip logic + `staged_payloads` persistence in `engine.py` (45 lines)
9. Staged downstream dep validator in `dry_run()` (20 lines)

### Step 2b — Prompt Rewrite: Staged Resources (~120 lines, 5 files)
10. `system_prompt.md`: generation rule 17 (staged), validation error, workflow question (~35 lines)
11. `decision_rubrics.md`: 4 per-type staged subsections + cross-cutting "Staged Resources" section (~55 lines)
12. `generation_profiles.md`: scope ladder row + quick mapping row (~5 lines)
13. `ordering_rules.md`: "Staged Resources and DAG Ordering" section with rules + example (~30 lines)
14. `README.md`: table update (~2 lines)

### Step 3 — UI: Templates + CSS (~360 lines, 5-6 tasks)
15. Run detail page: route in `webhooks.py` + `run_detail.html` (180 lines)
16. Fire endpoint in `webhooks.py` + `staged_row.html` (75 lines)
17. Standalone listener: route + `listen.html` + tunnel detection (60 lines)
18. Navigation: `base.html` tab, `runs.html` details link, `run_complete.html` link (7 lines)
19. `resource_row.html` staged status (8 lines)
20. CSS (60 lines)

Steps 1-9 are backend-only and testable via curl. Steps 10-14 are prompt
content. Steps 15-20 are UI.

---

## Testing Plan

1. **Config persistence**: Run a demo, verify `runs/<id>_config.json` exists
2. **Webhook receiver**: `curl -X POST http://localhost:8000/webhooks/mt -H 'Content-Type: application/json' -H 'X-Topic: payment_order' -d '{"event":"completed","data":{"id":"<id>","object":"payment_order"}}'` — verify JSONL written, correlation works
3. **Early correlation**: During execution, watch for `legal_entity` webhook arriving before run completes — verify it correlates (not in `_unmatched`)
4. **Webhook SSE**: Open `/webhooks/stream` in browser/curl, send webhook, verify SSE push
5. **Staged resources**: Run config with `staged: true` on POs, IPDs, EPs, and LTs, verify execute shows "Staged" for each, verify `_staged.json` has resolved UUIDs for all four types
6. **Staged dep validator**: Config where non-staged resource depends on staged resource → clear validation error (test reversal→staged PO, LT→staged PO via `ledgerable_id`)
7. **Fire endpoint**: `curl -X POST .../fire/payment_order.po_pay_alice -d 'api_key=...' -d 'org_id=...'` — verify PO created in MT. Fire IPD → verify polling completes and child refs harvested. Fire EP/LT → verify created in MT
8. **Prompt completeness**: Ask LLM "generate a marketplace demo with staged payments" using updated prompts → verify it produces valid `staged: true` configs; ask for "step-by-step demo" → verify it suggests staging; ask for config where PO reversal targets staged PO → verify LLM either stages both or doesn't stage the PO
9. **Test webhook button**: Click on listener page → dummy payload in live feed
10. **Tunnel detection**: Start ngrok → public URL shown. Stop → warning banner.
11. **Run detail**: All four tabs render correctly, SSE webhooks stream live
12. **End-to-end demo flow**: Execute with staged POs → run detail → fire → see in MT dashboard → webhook arrives → shown in webhooks tab

---

## Complexity & Risk Assessment

| Component | Complexity | Risk | Notes |
|-----------|-----------|------|-------|
| Config persistence | Trivial | None | |
| Webhook receiver + SDK sig verify | Low | None | SDK does crypto; read raw body once |
| Ring buffer + JSONL persistence | Low | None | `uvicorn --reload` clears buffer (ok) |
| Correlation index (flat dict) | Low | None | O(1) lookup |
| Inline index hook (callback) | Low | None | Preserves engine independence |
| Fan-out SSE stream | Medium | Disconnect cleanup | Same pattern as execute stream |
| Staged engine skip + validator (4 types) | Medium | Downstream dep edge cases | Dry-run validator is mandatory; `getattr` handles all types |
| Staged payload persistence | Low | File write after API success | Idempotency key prevents duplicates |
| Fire endpoint (PO, EP, LT) | Medium | Ref resolution from manifest | Simplified engine pattern, dispatch table |
| Fire endpoint (IPD) | Medium-high | Polling + child ref harvest | Reuses `_poll_ipd_status` from handlers.py |
| Prompt rewrite (staged) | Medium | Completeness, consistency across 5 files | Must stay in sync with schema; test via LLM generation |
| Run detail page | Medium | Template complexity, tab state | CSS show/hide keeps SSE alive |
| Standalone listener + tunnel detect | Low | ngrok API may change | Graceful fallback |
| CSS | Low | None | |

**Estimated effort: 2–3 days** with testing at each step.

---

## Review Fixes (from `plan/reviews/mt_dataloader_step1_step2_webhooks_review.md`)

| # | Issue | Severity | Resolution |
|---|-------|----------|------------|
| 1 | Validator misses child-ref deps on staged resources | Runtime crash | **Fixed in step2 plan** — `_dep_hits_staged()` checks parent ref |
| 2 | Validator misses staged→staged data-field deps | Runtime crash | **Fixed in step2 plan** — two-pass validator, staged resources not skipped |
| 3 | Missing `manifest.write()` after `record_staged()` | Data loss | **Fixed in step2 plan** — added to staged branch in `create_one()` |
| 4 | `build_dag()` may not include `depends_on` edges | Verify | **Non-issue** — `build_dag()` already handles `depends_on` at lines 218-220 (reviewer had pre-step-1 code) |
| 5 | Spec scaffold includes unused `import httpx` | Non-issue | Correct omission in implementation |
| 6 | Blocking I/O in `_persist_webhook` | Acceptable | Consistent with `manifest.write()` pattern |
| 7 | Sig client singleton doesn't invalidate on secret change | Acceptable | Server restart clears module state |
| 8 | `_webhook_listeners` type annotation | Minor | **Fixed in code** — `asyncio.Queue[WebhookEntry]` |
| 9 | Duplicate webhook returns plain dict | Non-issue | Correct FastAPI 200 behavior |
| 10 | `getattr` anti-pattern — move `staged` to base | Style | **Rejected** — `extra="forbid"` on `_BaseResourceConfig` gives free type safety; `staged` on base would silently accept staging of connections/LEs/etc. |
| 11 | Step 1 spec line numbers stale | Doc only | Not worth fixing; code snippets are correct |
| 12 | `config_hash` is staging-agnostic | Minor | By design — hash is for idempotency, not staging identity |
| 13 | Staged payloads lost on disconnect | By design | Documented — failed/disconnected runs don't need staged payloads |

---

## Design Decisions

**Why JSONL over SQLite?** Matches existing `runs/*.json` pattern. No new deps. Append-only is safe. Easy to inspect.

**Why fan-out queues over pub/sub?** Execute stream already uses `asyncio.Queue`. Same pattern. `encode/broadcaster` is archived. No lib improves this for single-process.

**Why flat correlation dict over nested?** O(1) vs O(runs). Single `created_id → (run_id, typed_ref)` dict is simpler and faster.

**Why callback on `execute()` over direct import?** Engine.py has "zero SDK dependency" (line 9). Callback preserves this. Wiring happens in `main.py`.

**Why not show webhooks on execute page?** `htmx-ext-sse` = one SSE per `sse-connect` attr. Execute SSE has a lifecycle (start → complete → close). `run_complete` partial links to run detail where webhooks have their own SSE. Simpler.

**Why `validate_signature()` over manual HMAC?** SDK ships the method. Zero custom crypto. ~2 lines.

**Why extract to `webhooks.py`?** `main.py` is 880 lines. `APIRouter` makes extraction trivial. Distinct concern.

**Why `staged` on model not separate section?** Inline in `payment_orders[]` = same DAG, same ref resolution, same preview table. Separate section would duplicate schema.

**Why `staged` on 4 individual types, not `_BaseResourceConfig`?** `extra="forbid"` on the base class naturally rejects `"staged": true` on non-stageable types (connections, LEs, IAs, etc.) with a clear Pydantic error. Moving it to the base would silently accept staging on all 16+ types — the engine would try to stage a connection, and the fire dispatch table wouldn't have a handler. The `getattr` in the engine is a small cost for free type safety.

**Why two-pass validator, not skip staged resources?** `resolve_refs()` runs before the staged check in `create_one()`. If staged resource A has a data-field `$ref:` to staged resource B, `resolve_refs(A)` crashes because B has no `created_id`. The validator must check staged resources' data-field refs against other staged resources. `depends_on` between staged resources is fine (ordering only).

**Why save staged payloads to disk?** Server may restart between run completion and demo. Disk is inspectable, debuggable, survives restarts. ~200 bytes per PO.

**Why a dedicated prompt rewrite step?** The LLM prompt kit is the only interface for generating configs. Without staged docs in the prompts, the LLM will never produce `staged: true` or will create invalid dependency chains. The rewrite must land after Step 2 (when schema changes exist) and before anyone generates staged configs. Touching 5 prompt files across rules, rubrics, profiles, and ordering requires its own tracking.

**Why CSS show/hide tabs over lazy-load?** Webhook SSE stays connected when switching tabs. Lazy-load would reconnect each time.

**Why fire from run detail not execute page?** Execute SSE closes on completion. Run detail is stable, refreshable, has its own webhook SSE.

---

## Library Decisions

| Library | Verdict |
|---------|---------|
| `sse-starlette` | Already in `requirements.txt`. No change. |
| `httpx` | Transitive dep of MT SDK. Pin explicitly in `requirements.txt` for tunnel detection. |
| MT SDK `client.webhooks.validate_signature()` | **Use this.** Sync method, no network call. |
| `encode/broadcaster` | Archived, overkill. **Skip.** |
| `fastapi_websocket_pubsub` | WebSocket-oriented. **Skip.** |

**New explicit dependencies: 0.** Pin `httpx` that's already installed.

---

## MT Webhook Reference (from docs)

### Payload structure
```json
{
  "event": "completed",
  "data": {
    "id": "0198860a-5655-7dbc-9cbd-6616a8090fa8",
    "object": "payment_order",
    "status": "completed",
    ...
  }
}
```

### Headers
| Header | Content | Use |
|--------|---------|-----|
| `X-Topic` | Resource type (e.g. `payment_order`) | Construct `event_type` |
| `X-Signature` | HMAC-SHA-256 hex digest | Signature verification |
| `X-Webhook-ID` | Unique per webhook (stable across retries) | Deduplication |
| `X-Live-Mode` | `true` or `false` | Filter test vs live |
| `X-Event-ID` | Event object ID | Audit trail |

### Topics
`balance_report`, `connection_legal_entity`, `decision`, `external_account`,
`expected_payment`, `incoming_payment_detail`, `internal_account`,
`ledger_account_settlement`, `ledger_transaction`, `legal_entity`,
`payment_order`, `return`, `reversal`, `transaction`, `user_onboarding`

### SDK signature verification
```python
from modern_treasury import AsyncModernTreasury

# Cached module-level singleton (created once, reused for all webhooks)
_sig_client: AsyncModernTreasury | None = None

def _get_sig_client(secret: str) -> AsyncModernTreasury:
    global _sig_client
    if _sig_client is None:
        _sig_client = AsyncModernTreasury(
            api_key="unused", organization_id="unused",
            webhook_key=secret,
        )
    return _sig_client

# Usage in receiver:
client = _get_sig_client(settings.webhook_secret)
is_valid = client.webhooks.validate_signature(
    payload=raw_body_str,
    headers=dict(request.headers),
)
```
Method signature: `validate_signature(payload: str, headers: HeadersLike, *, key: str | None = None) -> bool`

### MT webhook timeout
5 seconds for 2XX response. Longer → re-enqueued with exponential backoff.
5 consecutive days of failures → endpoint paused automatically.
