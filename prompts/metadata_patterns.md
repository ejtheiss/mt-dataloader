# Metadata Patterns

Metadata is business/demo data passed through to Modern Treasury unchanged.
Keys and values must be strings. The SE customizes metadata per-demo in the
config UI — keep generated configs minimal. The flow compiler injects trace
metadata automatically via `trace_key` / `trace_value_template`.

---

## Which Resources Support Metadata

| Supports metadata | Resource types |
|------------------|---------------|
| **Yes** | `legal_entity`, `ledger`, `counterparty`, `ledger_account`, `ledger_account_category`, `internal_account`, `external_account`, `virtual_account`, `expected_payment`, `payment_order`, `ledger_transaction`, `reversal` |
| **No** | `connection`, `incoming_payment_detail`, `return`, `category_membership`, `nested_category` |

Counterparty inline accounts (`accounts[]`) have their own metadata field
separate from the counterparty-level metadata. Inline accounts **do not** have a
`name` field — use `party_name` (and optional `metadata.account_label` or
similar) for display text; the **counterparty** row has `name`.

---

## What the Flow Compiler Provides Automatically

Each step in a `funds_flows` block gets trace metadata injected from the flow's
`trace_key` and `trace_value_template`:

```json
{
    "trace_key": "deal_id",
    "trace_value_template": "DEAL-{ref}-{instance}"
}
```

This produces metadata like `{"deal_id": "DEAL-marketplace__0042"}` on every
resource the flow creates. You do **not** need to manually add metadata to
individual steps for tracing — the compiler handles it.

---

## When to Add Metadata in a Config

Add metadata only when it conveys **structural or role information** that the SE
wants visible in the MT dashboard:

- `account_role` or `account_purpose` on internal accounts (e.g., `"fiat_collection"`, `"usdc_omnibus"`)
- `user_type` or `marketplace_role` on legal entities (e.g., `"seller"`, `"buyer"`)
- `sandbox_behavior`-related notes on counterparty accounts (handled by the `sandbox_behavior` field, not metadata)

One or two keys per resource is plenty. The SE adds vertical-specific metadata
(tenant IDs, policy numbers, ERP codes, etc.) directly in the config UI.

---

## Template Variables in Metadata and Descriptions

In Funds Flows, both `metadata` values and `description` fields support
template variables that are resolved per-instance during generation.

### Available Variables

| Variable | Resolves to | Example |
|----------|------------|---------|
| `{instance}` | Zero-padded instance number | `0042` |
| `{ref}` | Flow ref including instance suffix | `marketplace__0042` |
| `{business_name}` | Seeded company name | `Acme Corp` |
| `{first_name}` | Seeded first name | `John` |
| `{last_name}` | Seeded last name | `Doe` |
| `{industry}` | Seeded industry | `fintech` |
| `{country}` | Seeded country | `US` |

### Usage in step descriptions

```json
{
    "step_id": "ipd_deposit",
    "type": "incoming_payment_detail",
    "description": "ACH deposit from {first_name} {last_name}"
}
```

Variables are resolved by `deep_format_map` during compilation (runs on all
flows, not just those with `instance_resources`). Unknown variables produce
empty strings rather than errors, so typos fail silently.

---

## Rules

1. **Metadata values must be strings** — use `"250000"` not `250000`.
2. **Never put `$ref:` in metadata** — use `depends_on` for ordering and data
   fields for structural references.
3. **Keep it minimal** — the SE adds domain-specific keys in the config UI.
   Generated configs should have at most 1-2 metadata keys per resource.
4. **Don't duplicate trace metadata** — the flow compiler injects `trace_key`
   values automatically; don't re-add them manually.
