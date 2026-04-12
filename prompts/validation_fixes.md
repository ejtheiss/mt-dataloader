# Validation Fixes: Common Error Patterns

When `POST /api/validate-json` returns errors, use this guide to fix them.
Responses use **JSON API v1** (`schema_version: 1`): top-level `ok`, optional `phase`
(`parse` | `compile` | `dag` | …), and `errors[]` with `code`, `message`, and optional
`path`. **Pydantic schema issues** use the same strings as before as **`code`**
(e.g. `missing`, `extra_forbidden`) — treat like the old `type` field.

---

## Error types and fixes

### `missing` — Required field not provided

| `path` contains | Fix |
|----------------|-----|
| `receiving_account_id` | Add `receiving_account_id` ref for credit POs (`direction: credit` requires it) |
| `reconciliation_rule_variables` | Add EP rule variables: `internal_account_id`, `direction`, `amount_lower_bound`, `amount_upper_bound`, `type` |
| `legal_entity_id` | Every internal account must reference a legal entity |

### Execution / MT **422** — *Connection endpoint must be present* (`parameter: base`)

Usually **not** schema validation — MT rejected the **internal account** or
**legal entity** request against the **connection** (wrong `entity_id`, product
not enabled, or payload/rail mismatch). **Typical dataloader fix:** use **one**
`modern_treasury` connection and the **same** `connection_id` on all IAs for that
PSP (USD + USDC can share it — see `examples/stablecoin_ramp.json`). Confirm
`entity_id` is `modern_treasury` for default PSP demos (not BYOB unless
intended). For legal-entity create on PSP: **do not** put `connection_id` in
authored JSON (`decision_rubrics.md`). BYOB: include
`connection_id` on LE only when your scenario requires it.

### `ref` / `value_error` — Invalid ref format

`ref` must be a simple `snake_case` key — no dots, no `$ref:` prefix. The
engine auto-prefixes the resource type (e.g. ref `acme_corp` becomes
`legal_entity.acme_corp`).

### `extra_forbidden` — Unknown field

Check the schema for typos or unknown fields. Common causes:

- **`funds_flows[].display_title` / `display_summary`:** These are **valid** on each
  flow object (operator UI only; `GET /api/schema` lists them under `FundsFlowConfig`).
  If validate-json still reports them as `extra_forbidden`, the server is running an
  **older build** of the app whose `FundsFlowConfig` predates Plan 10c — redeploy or
  reinstall from the current repo so `models.flow_dsl.FundsFlowConfig` includes both fields.

- **Lifecycle rows you pasted at the root (`payment_orders`, `verify_external_accounts`,
  etc.):** Prefer **authoring** money movement and verification as **`funds_flows`**
  steps — the compiler emits the flat sections. Root arrays exist on the merged
  schema for compiled/edited JSON; hand-building them is easy to get wrong. If you
  meant micro-deposit verification, use step types `verify_external_account` /
  `complete_verification` under **`funds_flows`**, not a guessed root key
  (`decision_rubrics.md` § Root JSON).
- **`value_error` mixing wallet + bank sandbox on the same counterparty inline
  account:** Do **not** set **`sandbox_behavior`** together with
  **`wallet_account_number_type`** (or explicit stablecoin wallet
  **`account_details`**). Bank demos use `sandbox_behavior`; stablecoin wallet CPs
  use `wallet_account_number_type` or explicit `account_details` + network
  **`account_number_type`** — see **`decision_rubrics.md`** § *Stablecoin wallet accounts*.
- **`name` on `counterparties[].accounts[]`:** Remove it — the schema uses
  `extra="forbid"` on inline accounts. Use `party_name` or `metadata` for
  labels; the parent counterparty has `name`.
- **`effective_date` on an `incoming_payment_detail` step:** IPD uses
  `as_of_date`, not `effective_date`. Only `payment_order` and
  `ledger_transaction` accept `effective_date`.
- **`receiving_account_id` on an `incoming_payment_detail` step:** IPD uses
  `internal_account_id`, not `receiving_account_id`.
- **`originating_account_id` on a raw `incoming_payment_details[]` item:** Remove
  it — not in the resource schema (optional `originating_account_number` /
  `originating_routing_number` only for some rails). **`funds_flows`** IPD
  **steps** may include `originating_account_id` (DSL only).
- **`originating_account_id` on a raw `expected_payments[]` item:** Remove — not
  in the EP resource model.
- **Wrong field on any `funds_flows` step:** Each step `type` has a strict
  set of allowed fields. See the step field reference table in the prompt.
- **`extra_forbidden` on `external_account_id` in `verify_external_account` or
  `complete_verification` steps:** Use **`external_account_ref`** instead (e.g.
  `@actor:user_2.bank` or `$ref:external_account.<key>`). The loader IR does not
  use `external_account_id` on these step payloads.
- **`extra_forbidden` on `sandbox_behavior` in `external_accounts[]`:** Remove
  it. **`sandbox_behavior`** is only valid on **counterparty inline `accounts[]`**,
  not on standalone **`external_accounts[]`** rows.
- **`extra_forbidden` on `description` or `timing` inside flat
  `verify_external_accounts[]`, `complete_verifications[]`, or `archive_resources[]`:**
  Those keys are **not** on the emitted resource schema. If you **hand-pasted**
  compiled-style rows, remove them. If you authored **`funds_flows`** steps with
  `description` / `timing`, a current compiler strips them on emit — if errors
  persist, upgrade the running **dataloader** build **or** remove `description` /
  `timing` from those steps (safest default for generated configs).

### `address_types` / `identifications` / `documents` on legal entities

**Remove these fields entirely.** The dataloader always overwrites them with
compliant mock data. For a business: only `ref`, `legal_entity_type`,
`business_name`. For an individual: only `ref`, `legal_entity_type`,
`first_name`, `last_name`.

### `string_type` in metadata

Metadata values must be strings. Use `"250000"` not `250000`.

### `staged_dependency`

A non-staged resource depends (via `$ref:` or `depends_on`) on a staged
resource or its child ref. Fix: restructure so non-staged resources only
reference non-staged ones, or mark the dependent resource as `staged: true`.

**Common with `complete_verification`:** The **DSL** defaults **`staged: true`**. If
**PO/IPD** list that step in **`depends_on`**, set **`"staged": false`** on the
**`complete_verification`** step (happy path) or stage the payment steps too. Setup
**`/api/validate-json`** returns **`code: staged_dependency`** (phase `dag`) for this case
(see **`04_validation_observability.md`** § Interim shipped).

### `staged_data_ref`

A staged resource has a data-field `$ref:` pointing at another staged
resource. Fix: remove the data-field ref (the ID won't exist yet) and use
`depends_on` for ordering between staged items instead.

### `unresolvable_ref`

A `$ref:` string points to a resource that doesn't exist in the config.
Check spelling, ensure the target resource is defined, and verify the ref
format: `$ref:<resource_type>.<ref_key>` (e.g. `$ref:internal_account.main`).

For child refs, use the correct selector:
- `$ref:counterparty.<key>.account[0]` (0-indexed)
- `$ref:internal_account.<key>.ledger_account`
- `$ref:payment_order.<key>.ledger_transaction`

### `cycle_error`

Circular dependency in the DAG. Two or more resources depend on each other.
Break the cycle by removing one `depends_on` or `$ref:` edge.

---

## Funds Flows errors

When the config includes `funds_flows`, the compiler validates the flow
definitions and reports errors with `path` pointing at the flow.

### `duplicate_step_id`

Two steps (or optional group steps) share the same `step_id` within one
flow. Fix: rename one to be unique.

### `unbalanced_ledger_entries`

A step's `ledger_entries[]` has unequal debit and credit totals. Fix:
adjust amounts so total debits equal total credits.

### `unknown_step_type`

A step's `type` is not one of: `payment_order`, `incoming_payment_detail`,
`expected_payment`, `ledger_transaction`, `return`, `reversal`,
`transition_ledger_transaction`, `verify_external_account`,
`complete_verification`, `archive_resource`. Fix: use a valid type.

### `missing_status` on `transition_ledger_transaction`

A `transition_ledger_transaction` step requires a `status` field
(`pending`, `posted`, or `archived`). Fix: add the `status` field.

### `invalid_depends_on`

A step's `depends_on` references a `step_id` that doesn't exist in the
flow. Fix: check spelling or ensure the target step is defined.

---

## Repair workflow

1. Read each error's `path` to locate the resource and field.
2. Apply the fix based on `type` (see above).
3. Return a **full** replaced JSON document — do not return a diff or partial.
4. If multiple errors, fix all of them in one pass.
5. Re-validate until `valid: true`.
