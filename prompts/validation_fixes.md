# Validation Fixes: Common Error Patterns

When `POST /api/validate-json` returns errors, use this guide to fix them.
Each error has a `path`, `type`, and `message`.

---

## Error types and fixes

### `missing` — Required field not provided

| `path` contains | Fix |
|----------------|-----|
| `receiving_account_id` | Add `receiving_account_id` ref for credit POs (`direction: credit` requires it) |
| `reconciliation_rule_variables` | Add EP rule variables: `internal_account_id`, `direction`, `amount_lower_bound`, `amount_upper_bound`, `type` |
| `legal_entity_id` | Every internal account must reference a legal entity |

### `ref` / `value_error` — Invalid ref format

`ref` must be a simple `snake_case` key — no dots, no `$ref:` prefix. The
engine auto-prefixes the resource type (e.g. ref `acme_corp` becomes
`legal_entity.acme_corp`).

### `extra_forbidden` — Unknown field

Check the schema for typos or unknown fields. Common causes:

- **`name` on `counterparties[].accounts[]`:** Remove it — the schema uses
  `extra="forbid"` on inline accounts. Use `party_name` or `metadata` for
  labels; the parent counterparty has `name`.
- **`effective_date` on an `incoming_payment_detail` step:** IPD uses
  `as_of_date`, not `effective_date`. Only `payment_order` and
  `ledger_transaction` accept `effective_date`.
- **`receiving_account_id` on an `incoming_payment_detail` step:** IPD uses
  `internal_account_id`, not `receiving_account_id`.
- **Wrong field on any `funds_flows` step:** Each step `type` has a strict
  set of allowed fields. See the step field reference table in the prompt.

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
`transition_ledger_transaction`. Fix: use a valid type.

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
