# Naming Conventions for Config Refs

All resource `ref` values must follow these patterns. Consistent naming makes
`$ref:` references predictable and reduces generation errors.

---

## General Rules

1. **Simple keys only** — no dots, no `$ref:` prefix. The engine auto-prefixes
   the resource type (e.g. ref `acme_corp` becomes `legal_entity.acme_corp`).
2. **lowercase_snake_case** — letters, digits, underscores only.
3. **Unique within type** — two counterparties cannot share a ref, but a
   counterparty and a legal entity can both use `acme_corp`.
4. **Descriptive** — the ref should tell you what the resource *is* without
   looking at other fields.

---

## Per-Type Patterns

| Resource type | Pattern | Good examples | Bad examples |
|--------------|---------|---------------|-------------|
| `connection` | `<bank>_bank` | `modern_treasury_bank`, `gringotts_bank`, `iron_bank` | `conn1`, `my_connection` |
| `legal_entity` | `<company_or_person>` | `acme_corp`, `alice_johnson`, `boats_group_inc` | `le_1`, `entity` |
| `counterparty` | `<role>_<name>_cp` or `<role>_<name>` | `vendor_cloudhost`, `buyer_owen_cp`, `seller_bluewater_cp` | `cp1`, `counterparty_a` |
| `internal_account` | `<owner>_wallet` or `<purpose>` | `buyer_owen_wallet`, `seller_bluewater_wallet`, `boats_group_revenue`, `main_checking` | `ia_1`, `account` |
| `external_account` | `<owner>_<bank_or_purpose>` | `alice_secondary`, `vendor_chase_acct` | `ea1` |
| `ledger` | `<scope>` or `main` | `main`, `marketplace_ledger` | `ledger1` |
| `ledger_account` | `<accounting_concept>` | `cash`, `accounts_payable`, `revenue`, `refunds` | `la_1` |
| `ledger_account_category` | `<category_name>` | `assets`, `liabilities`, `revenue_category` | `cat1` |
| `virtual_account` | `va_<payer_or_purpose>` | `va_alice`, `va_tenant_1001` | `virtual1` |
| `expected_payment` | `ep_<payer>_<purpose>` | `ep_alice_payment`, `ep_buyer_deposit` | `ep1` |
| `payment_order` | `po_<action>` | `po_pay_bob`, `po_platform_fee`, `po_seller_payout` | `payment1` |
| `incoming_payment_detail` | `ipd_<source>_<purpose>` | `ipd_from_alice`, `ipd_buyer_deposit` | `ipd1` |
| `ledger_transaction` | `lt_<purpose>` | `lt_revenue_recognition`, `lt_fee_accrual` | `lt1` |
| `return` | `return_<ipd_ref>` | `return_ipd_from_alice`, `return_nsf_deposit` | `ret1` |
| `reversal` | `reverse_<po_ref>` | `reverse_po_pay_bob` | `rev1` |
| `transition_ledger_transaction` | `<action>_<lt_ref>` | `post_lt_settle`, `archive_lt_revenue` | `tlt1` |
| `category_membership` | `<account>_in_<category>` | `cash_in_assets`, `ar_in_assets` | `mem1` |
| `nested_category` | `<child>_under_<parent>` | `liabilities_under_assets` | `nest1` |

---

## $ref: Target Patterns

When referencing another resource, always use the fully qualified typed ref:

```
$ref:<resource_type>.<ref_key>
```

### Direct resource refs

```
$ref:connection.modern_treasury_bank
$ref:connection.gringotts_bank
$ref:legal_entity.acme_corp
$ref:counterparty.vendor_cloudhost
$ref:internal_account.main_checking
$ref:ledger.main
$ref:ledger_account.cash
$ref:payment_order.po_pay_bob
$ref:incoming_payment_detail.ipd_buyer_deposit
```

### Child refs (auto-registered by handlers)

Some handlers register child refs after creation. Reference them with a
dotted selector appended to the parent ref:

| Parent type | Child selector | Example |
|------------|---------------|---------|
| counterparty | `account[N]` | `$ref:counterparty.vendor_bob.account[0]` |
| internal_account | `ledger_account` | `$ref:internal_account.main_checking.ledger_account` |
| external_account | `ledger_account` | `$ref:external_account.alice_secondary.ledger_account` |
| virtual_account | `ledger_account` | `$ref:virtual_account.va_alice.ledger_account` |
| incoming_payment_detail | `transaction` | `$ref:incoming_payment_detail.ipd_buyer_deposit.transaction` |
| incoming_payment_detail | `ledger_transaction` | `$ref:incoming_payment_detail.ipd_buyer_deposit.ledger_transaction` |
| payment_order | `ledger_transaction` | `$ref:payment_order.po_pay_bob.ledger_transaction` |

`account[N]` is 0-indexed and matches the order of accounts in the
counterparty's `accounts[]` array.

`payment_order.ledger_transaction` is only available when the PO includes
an inline `ledger_transaction` field. If the PO has no inline ledger
transaction, this child ref is not registered.

---

## Anti-Patterns

- **Generic names**: `entity1`, `cp_a`, `po_1` — unreadable and unmaintainable
- **Dots in ref**: `acme.corp` — the ref validator rejects dots
- **$ref: prefix in ref field**: `$ref:legal_entity.acme` — ref is just the
  key, not the full typed reference
- **CamelCase or UPPER_CASE**: `AcmeCorp`, `MAIN_CHECKING` — use snake_case
- **Reusing a ref across types with ambiguous names**: if both a counterparty
  and legal entity are named `acme`, that's fine technically but confusing.
  Prefer `acme_corp` for the LE and `vendor_acme_cp` for the CP.
