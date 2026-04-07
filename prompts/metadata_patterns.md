# Metadata Patterns

Metadata is business/demo data passed through to Modern Treasury unchanged.
Keys and values must be strings. The SE customizes metadata per-demo in the
config UI — keep generated configs minimal. The flow compiler injects trace
metadata automatically via `trace_key` / `trace_value_template`.

---

## Which Resources Support Metadata


| Supports metadata | Resource types                                                                                                                                                                                                          |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Yes**           | `legal_entity`, `ledger`, `counterparty`, `ledger_account`, `ledger_account_category`, `internal_account`, `external_account`, `virtual_account`, `expected_payment`, `payment_order`, `ledger_transaction`, `reversal` |
| **No**            | `connection`, `incoming_payment_detail`, `return`, `category_membership`, `nested_category`                                                                                                                             |


Counterparty inline accounts (`accounts[]`) have their own metadata field
separate from the counterparty-level metadata. Inline accounts **do not** have a
`name` field — use `party_name` (and optional `metadata.account_label` or
similar) for display text; the **counterparty** row has `name`.

---

## Trace metadata (from the flow)

Each `funds_flows` step gets trace metadata from the flow's `trace_key` and
`trace_value_template`:

```json
{
    "trace_key": "deal_id",
    "trace_value_template": "DEAL-{ref}-{instance}"
}
```

Example result: `{"deal_id": "DEAL-marketplace__0042"}` on emitted resources.
Do not duplicate trace keys on individual steps unless you need extras beyond the template.

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

### Multi-`user_N` (scaling) — placeholder rule

If a flow has **two or more** `frame_type: "user"` actors (`user_1`, `user_2`, …), every **`instance_resources`** field that names a party (`business_name` on LEs, `name` on counterparties, `party_name` on inline accounts / external accounts, etc.) must use **actor-scoped** placeholders keyed to the **`actors`** map: **`{user_1_business_name}`**, **`{user_2_business_name}`**, **`{user_1_name}`**, … — matching which **`user_N.entity_ref`** owns that `legal_entity` / `counterparty` / account row.

**Do not** paste **`{business_name}`** on more than one such row when those rows belong to **different** `user_N` frames. The engine treats global `{business_name}` as **one** default string per instance; repeating it makes payor and payee identical.

Role-shaped LE refs (`general_contractor_{instance}`, `subcontractor_{instance}`, …) are fine; **placeholder choice** is what must follow this rule.

### Available variables


| Variable | Resolves to | Example |
| -------- | ----------- | ------- |
| `{instance}` | Zero-padded instance number | `0042` |
| `{ref}` | Flow ref including instance suffix | `marketplace__0042` |
| `{user_1_business_name}`, `{user_2_business_name}`, … | Seeded company for that **`actors`** key | (per actor) |
| `{user_1_first_name}`, `{user_2_first_name}`, … | Seeded first name for that actor | (per actor) |
| `{user_1_name}`, `{user_2_name}`, … | Rendered display string for that actor | (per actor) |
| `{business_name}` | **Single** default company string for the instance | `Acme Corp` |
| `{first_name}`, `{last_name}` | Default merged individual fields (single-party use) | `John`, `Doe` |
| `{industry}`, `{country}` | Seeded profile fields | `fintech`, `US` |

**`{business_name}`:** use only when **at most one** variable business party in that instance uses it, or for descriptions / non-party text. For two business parties, use **`{user_1_business_name}`** and **`{user_2_business_name}`** (and mirror those in CP `name` / `party_name`).

If two `user_N` frames still collide on names, set **different `dataset`** on each actor frame (optional) — see schema / examples.

### Usage in step descriptions

```json
{
    "step_id": "ipd_deposit",
    "type": "incoming_payment_detail",
    "description": "ACH deposit from {first_name} {last_name}"
}
```

Placeholders in descriptions resolve at **compile** on all flows. Unknown
placeholder names become **empty strings** — verify spelling against seed keys.

---

## Rules

1. **Metadata values must be strings** — use `"250000"` not `250000`.
2. **Never put `$ref:` in metadata** — use `depends_on` for ordering and data
  fields for structural references.
3. **Keep it minimal** — the SE adds domain-specific keys in the config UI.
  Generated configs should have at most 1-2 metadata keys per resource.
4. **Don't duplicate trace metadata** — `trace_key` / `trace_value_template` on the flow already set trace fields on emitted resources.

