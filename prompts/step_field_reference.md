# Funds Flow Step Field Reference

Almost every step shares authoring-time fields: `step_id`, `type`, `description`,
`depends_on`, `timing`, `metadata`. **Not all of those survive onto every compiled
flat resource:** for `verify_external_account`, `complete_verification`, and
`archive_resource`, the flat rows **do not** include `description` or `timing`. A
current emitter **strips** those keys on emit (same pattern as `description` on
return/reversal/TLT) while keeping them on internal IR for Mermaid.

**Default for generation:** **omit** `description` and `timing` on verify, complete,
and archive steps unless you **explicitly** want richer Mermaid labels — that
avoids relying on a particular compiler build and matches minimal valid payloads.

**`staged` on `complete_verification`:** The **DSL** field defaults **`true`** in
Pydantic, but **`complete_verification`** is **not** a UI-fireable staged type (only
PO / IPD / EP / LT are — see **`dataloader/staged_fire.py`**). If **PO, IPD, EP, or
LT** steps **`depends_on`** this step in the **same** load, generated JSON should
include **`"staged": false`** (happy-path demo) unless the user explicitly wants a
**staged** verification workflow (then keep dependents staged too —
**`ordering_rules.md`**). **`verify_external_account`** and **`archive_resource`** do
not use the same **`staged`** semantics as payment steps; sequence with **`depends_on`**.
If you **do** include `description` and still see `extra_forbidden` on compiled flat
rows, the running app likely lacks the strip pass; upgrade the emitter or remove
those fields from the authored steps.

**Authoring vs flat config:** `verify_external_account`, `complete_verification`, and
`archive_resource` are **authored only** as `**funds_flows[].steps`** (and
`optional_groups`). The compiler lowers them into top-level `**verify_external_accounts[]**`,
`**complete_verifications[]**`, and `**archive_resources[]**` on the flat
`DataLoaderConfig` — same lifecycle pattern as `payment_order` → `payment_orders[]`.
Do **not** hand-write those root arrays in model-generated JSON unless you are **editing
compiled output** (`decision_rubrics.md` § Root JSON, *Flat vs authoring*).

## Raw `incoming_payment_details[]` vs Funds Flow IPD steps

Top-level `**incoming_payment_details`** objects (hand-written or pasted JSON)
**must not** include `originating_account_id` — the schema has
`internal_account_id` plus optional `originating_account_number` /
`originating_routing_number` where needed. `**funds_flows` steps** with
`type: incoming_payment_detail` **may** include `originating_account_id`
(external sender ref); the compiler drops it when emitting resources (the MT
simulation API does not take that field on the saved IPD object the same way).

## Type-specific fields


| `type`                          | Type-specific fields                                                                                                                                                                                                                                                |
| ------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `payment_order`                 | `payment_type`, `direction`, `amount`, `originating_account_id`, `receiving_account_id`, `currency`, `statement_descriptor`, `**effective_date`**, `staged`, `ledger_entries`, `ledger_inline`, `ledger_status`                                                     |
| `incoming_payment_detail`       | `payment_type`, `amount`, `originating_account_id`, `internal_account_id`, `direction` (always `"credit"`), `currency`, `virtual_account_id`, `**as_of_date**` (**NOT** `effective_date`), `fulfills`, `staged`, `ledger_entries`, `ledger_inline`, `ledger_status` |
| `expected_payment`              | `amount`, `direction`, `originating_account_id`, `internal_account_id`, `currency`, `date_lower_bound`, `date_upper_bound`, `staged`, `ledger_entries`, `ledger_inline`, `ledger_status`                                                                            |
| `ledger_transaction`            | `ledger_entries` (required), `ledger_status`, `effective_at`, `**effective_date`**, `staged`                                                                                                                                                                        |
| `return`                        | `returnable_id`, `code`, `reason`, `ledger_entries`, `ledger_inline`, `ledger_status`                                                                                                                                                                               |
| `reversal`                      | `payment_order_id`, `reason`, `ledger_entries`, `ledger_inline`, `ledger_status`                                                                                                                                                                                    |
| `transition_ledger_transaction` | `ledger_transaction_id`, `status` (required)                                                                                                                                                                                                                        |
| `verify_external_account`       | `**external_account_ref**` (required), `originating_account_id`, `payment_type` (default `"rtp"`), `currency`, `priority`                                                                                                                                           |
| `complete_verification`         | `**external_account_ref**` (required), optional **`staged`** — Pydantic default **`true`**; **generated configs should use `false`** when downstream money steps `depends_on` this step unless the user wants a staged verification chain                              |
| `archive_resource`              | `resource_type`, `**resource_ref**` (required), `archive_method` (`delete` / `archive` / `request_closure`, default `delete`)                                                                                                                                       |


**Do not** use `external_account_id` on `verify_external_account` or `complete_verification` steps — the loader IR uses `**external_account_ref`** only; extra fields are rejected.

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
  "staged": false
}
```

## `instance_resources` + `user_N` (step refs)

If steps use **`@actor:user_N.<slot>`**, that **`user_N`** must be **`frame_type: "user"`** with **`entity_ref`**: `$ref:legal_entity.<key>` where **`<key>` matches an `instance_resources.legal_entities[].ref` on the same `funds_flows[]` entry** and **includes `{instance}`**. Party **`slots`** (`bank`, wallet, etc.) must **`$ref:`** **`counterparty` / `external_account` / `internal_account` keys** likewise defined under **that flow’s `instance_resources`** (with `{instance}`), except the **fixed reused participant** case in **`system_prompt.md` → *User actors (mandatory JSON)***. **`direct_N`** uses top-level static resources and **`customer_name`** only — no `entity_ref`.

**Placeholders in `instance_resources`:** with **multiple `user_N`**, use **actor-scoped** `{user_1_business_name}`, `{user_2_business_name}`, … for LE / CP / `party_name` rows (not repeated bare `{business_name}`). Globals — see **`metadata_patterns.md`** § *Multi-`user_N` (scaling)* and **`system_prompt.md`**.

## Easy field errors

- IPD uses `as_of_date`, NOT `effective_date`. PO and LT use `effective_date`.
- IPD uses `internal_account_id`, NOT `receiving_account_id`. PO uses `receiving_account_id`.
- Do **not** put `originating_account_id` on **raw** `incoming_payment_details[]`
rows (schema forbids it). It is only for **DSL** IPD steps under `funds_flows`.
- IPD `direction` is always `"credit"`. For ACH collections use a PO with `direction: "debit"`.
- ACH debit PO: `originating_account_id` = IA receiving funds, `receiving_account_id` = EA being debited.
- **`sandbox_behavior`** belongs only on **counterparty inline `accounts[]`**, not on **`external_accounts[]`** standalone resources.
- **Stablecoin wallet counterparties:** inline `accounts[]` use **`wallet_account_number_type`** (sandbox demo address) **or** explicit **`account_details`** with `account_number` + network **`account_number_type`** (`ethereum_address`, `base_address`, `polygon_address`, `arbitrum_one_address`, `solana_address`, `stellar_address`). **No** `routing_details`. **Never** combine **`sandbox_behavior`** with wallet helpers on the same account. Full MT-aligned shape: **`decision_rubrics.md`** § *Stablecoin wallet accounts*.

## Step types summary


| `type`                          | Resource    | Notes                                        |
| ------------------------------- | ----------- | -------------------------------------------- |
| `payment_order`                 | PO          | Set `payment_type` + `direction`             |
| `incoming_payment_detail`       | IPD         | Sandbox inbound sim                          |
| `expected_payment`              | EP          | Reconciliation matcher                       |
| `ledger_transaction`            | LT          | Standalone double-entry                      |
| `return`                        | Return      | IPD return                                   |
| `reversal`                      | Reversal    | PO reversal                                  |
| `transition_ledger_transaction` | TLT         | Status change on existing LT                 |
| `verify_external_account`       | EA verify   | Sends micro-deposits (default RTP)           |
| `complete_verification`         | EA complete | Reads amounts + confirms; **prefer `staged: false`** in generated JSON when PO/IPD depend on it (see table above) |
| `archive_resource`              | Cleanup     | Delete / archive / close a resource          |


