# Funds Flow Step Field Reference

Every step shares: `step_id`, `type`, `description`, `depends_on`, `timing`, `metadata`.

**Root JSON:** Not every `type` has a matching top-level `DataLoaderConfig` array.
`verify_external_account`, `complete_verification`, and `archive_resource` are
**steps only** — never emit `verify_external_accounts[]`, `complete_verifications[]`,
or `archive_resources[]` at the root (`decision_rubrics.md` § Root JSON).

## Raw `incoming_payment_details[]` vs Funds Flow IPD steps

Top-level **`incoming_payment_details`** objects (hand-written or pasted JSON)
**must not** include `originating_account_id` — the schema has
`internal_account_id` plus optional `originating_account_number` /
`originating_routing_number` where needed. **`funds_flows` steps** with
`type: incoming_payment_detail` **may** include `originating_account_id`
(external sender ref); the compiler drops it when emitting resources (the MT
simulation API does not take that field on the saved IPD object the same way).

## Type-specific fields

| `type` | Type-specific fields |
|--------|---------------------|
| `payment_order` | `payment_type`, `direction`, `amount`, `originating_account_id`, `receiving_account_id`, `currency`, `statement_descriptor`, **`effective_date`**, `staged`, `ledger_entries`, `ledger_inline`, `ledger_status` |
| `incoming_payment_detail` | `payment_type`, `amount`, `originating_account_id`, `internal_account_id`, `direction` (always `"credit"`), `currency`, `virtual_account_id`, **`as_of_date`** (**NOT** `effective_date`), `fulfills`, `staged`, `ledger_entries`, `ledger_inline`, `ledger_status` |
| `expected_payment` | `amount`, `direction`, `originating_account_id`, `internal_account_id`, `currency`, `date_lower_bound`, `date_upper_bound`, `staged`, `ledger_entries`, `ledger_inline`, `ledger_status` |
| `ledger_transaction` | `ledger_entries` (required), `ledger_status`, `effective_at`, **`effective_date`**, `staged` |
| `return` | `returnable_id`, `code`, `reason`, `ledger_entries`, `ledger_inline`, `ledger_status` |
| `reversal` | `payment_order_id`, `reason`, `ledger_entries`, `ledger_inline`, `ledger_status` |
| `transition_ledger_transaction` | `ledger_transaction_id`, `status` (required) |
| `verify_external_account` | **`external_account_ref`** (required), `originating_account_id`, `payment_type` (default `"rtp"`), `currency`, `priority` |
| `complete_verification` | **`external_account_ref`** (required), `staged` (default `true`) |
| `archive_resource` | `resource_type`, **`resource_ref`** (required), `archive_method` (`delete` / `archive` / `request_closure`, default `delete`) |

**Do not** use `external_account_id` on `verify_external_account` or `complete_verification` steps — the loader IR uses **`external_account_ref`** only; extra fields are rejected.

## Canonical examples (verification steps)

```json
{
  "step_id": "verify_payee_bank",
  "type": "verify_external_account",
  "external_account_ref": "$ref:external_account.payee_payout",
  "originating_account_id": "@actor:direct_1.payments",
  "payment_type": "ach"
}
```

```json
{
  "step_id": "complete_payee_bank_verification",
  "type": "complete_verification",
  "depends_on": ["verify_payee_bank"],
  "external_account_ref": "$ref:external_account.payee_payout",
  "staged": true
}
```

## Common field mistakes

- IPD uses `as_of_date`, NOT `effective_date`. PO and LT use `effective_date`.
- IPD uses `internal_account_id`, NOT `receiving_account_id`. PO uses `receiving_account_id`.
- Do **not** put `originating_account_id` on **raw** `incoming_payment_details[]`
  rows (schema forbids it). It is only for **DSL** IPD steps under `funds_flows`.
- IPD `direction` is always `"credit"`. For ACH collections use a PO with `direction: "debit"`.
- ACH debit PO: `originating_account_id` = IA receiving funds, `receiving_account_id` = EA being debited.
- **`sandbox_behavior`** belongs only on **counterparty inline `accounts[]`**, not on **`external_accounts[]`** standalone resources.

## Step types summary

| `type` | Resource | Notes |
|--------|----------|-------|
| `payment_order` | PO | Set `payment_type` + `direction` |
| `incoming_payment_detail` | IPD | Sandbox inbound sim |
| `expected_payment` | EP | Reconciliation matcher |
| `ledger_transaction` | LT | Standalone double-entry |
| `return` | Return | IPD return |
| `reversal` | Reversal | PO reversal |
| `transition_ledger_transaction` | TLT | Status change on existing LT |
| `verify_external_account` | EA verify | Sends micro-deposits (default RTP) |
| `complete_verification` | EA complete | Reads amounts + confirms (staged by default) |
| `archive_resource` | Cleanup | Delete / archive / close a resource |
