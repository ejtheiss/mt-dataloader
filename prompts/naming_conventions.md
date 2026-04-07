# Naming Conventions for Config Refs

Follow the SE's naming from their spec or materials. Keep refs simple, clean,
and domain-appropriate. Do not over-hydrate — short, obvious names are better
than long, overly-descriptive ones.

---

## General Rules

1. **Simple keys only** — no dots, no `$ref:` prefix. The engine auto-prefixes
   the resource type (e.g. ref `acme` becomes `legal_entity.acme`).
2. **lowercase_snake_case** — letters, digits, underscores only.
3. **Unique within type** — two counterparties cannot share a ref, but a
   counterparty and a legal entity can both use `acme`.
4. **Follow the SE's language** — use names from the SE's spec, slide deck, or
   domain. Don't invent elaborate naming schemes.

### Instance-scoped refs (`_{instance}`)

Under **`funds_flows[].instance_resources`**, `ref` keys should include **`{instance}`** so scaled copies do not collide (e.g. `payor_{instance}`, `general_contractor_{instance}`). That is **naming of keys**, separate from **display placeholders**: synthetic display names for **multiple `user_N`** must use **actor-scoped** template variables per **`metadata_patterns.md`**, not duplicated `{business_name}`.

---

## Per-Type Patterns

| Resource type | Pattern | Good examples | Bad examples |
|--------------|---------|---------------|-------------|
| `connection` | `<bank>` | `chase`, `silicon_valley_bank` | `conn1`, `my_connection` |
| `legal_entity` | `<name>` | `acme`, `alice_johnson`, `psp_operator`, `acme_payments` | `le_1`, `entity`, **`platform`** (too vague — use `platform_entity` / company-shaped ref) |
| `counterparty` | `<name>` | `cloudhost`, `owen`, `bluewater` | `cp1`, `counterparty_a` |
| `internal_account` | `<actor_name>` | `owen`, `bluewater`, `platform_ops` | `ia_1`, `buyer_owen_wallet` |
| `external_account` | `<owner>` | `alice`, `vendor_chase` | `ea1` |
| `ledger` | `<scope>` | `main`, `marketplace` | `ledger1` |
| `ledger_account` | `<concept>` | `cash`, `revenue`, `payable` | `la_1` |
| `ledger_account_category` | `<category>` | `assets`, `liabilities` | `cat1` |
| `virtual_account` | `<purpose>` | `alice`, `tenant_1001` | `virtual1` |
| `expected_payment` | `<payer>_<purpose>` | `alice_deposit`, `rent_march` | `ep1` |
| `payment_order` | `<action>` | `pay_bob`, `platform_fee`, `payout` | `payment1` |
| `incoming_payment_detail` | `<source>` | `from_alice`, `buyer_deposit` | `ipd1` |
| `ledger_transaction` | `<purpose>` | `settle`, `fee_accrual` | `lt1` |
| `return` | `<context>` | `nsf_deposit`, `return_alice` | `ret1` |
| `reversal` | `<context>` | `reverse_pay_bob` | `rev1` |
| `transition_ledger_transaction` | `<action>_<lt>` | `post_settle`, `archive_revenue` | `tlt1` |
| `category_membership` | `<account>_in_<category>` | `cash_in_assets` | `mem1` |
| `nested_category` | `<child>_under_<parent>` | `liabilities_under_assets` | `nest1` |
| `ledger_account_settlement` | `<from>_to_<to>` | `payable_to_cash` | `las1` |
| `ledger_account_balance_monitor` | `<account>_<condition>` | `cash_below_min` | `bm1` |
| `ledger_account_statement` | `<account>_<period>` | `cash_q1` | `stmt1` |
| `legal_entity_association` | `<child>_under_<parent>` | `alice_under_acme` | `lea1` |

**Internal accounts**: The ref is typically just the actor name (e.g., `owen`,
`bluewater`, `platform_ops`) since the connection info and currency are
appended by the MT API and visible in the dashboard. Don't repeat banking
details in the ref.

---

## $ref: Syntax

```
$ref:<resource_type>.<ref_key>
```

Examples: `$ref:connection.chase`, `$ref:legal_entity.acme`,
`$ref:internal_account.owen`, `$ref:ledger.main`

### Child refs (auto-registered by handlers)

Some handlers register child refs after creation:

| Parent type | Child selector | Example |
|------------|---------------|---------|
| counterparty | `account[N]` | `$ref:counterparty.owen.account[0]` |
| internal_account | `ledger_account` | `$ref:internal_account.owen.ledger_account` |
| external_account | `ledger_account` | `$ref:external_account.alice.ledger_account` |
| virtual_account | `ledger_account` | `$ref:virtual_account.alice.ledger_account` |
| incoming_payment_detail | `transaction` | `$ref:incoming_payment_detail.buyer_deposit.transaction` |
| incoming_payment_detail | `ledger_transaction` | `$ref:incoming_payment_detail.buyer_deposit.ledger_transaction` |
| payment_order | `ledger_transaction` | `$ref:payment_order.pay_bob.ledger_transaction` |

`account[N]` is 0-indexed matching the order in the counterparty's `accounts[]` array.

---

## Anti-Patterns

- **Over-hydrated names**: `buyer_owen_marketplace_wallet_usd` — just use `owen`
- **Prefixes that repeat the type**: `ia_main_checking`, `po_payment_1` — the type is already in the `$ref:`
- **Generic names**: `entity1`, `cp_a`, `po_1`
- **Dots in ref**: `acme.corp` — the ref validator rejects dots
- **`$ref:` prefix in ref field**: ref is just the key, not the full typed reference
- **CamelCase or UPPER_CASE**: `AcmeCorp`, `MAIN_CHECKING` — use snake_case
