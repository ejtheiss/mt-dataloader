# Metadata Patterns by Vertical

Metadata is business/demo data passed through to Modern Treasury unchanged.
Keys and values must be strings. Use metadata to make demos feel real and
grounded in the customer's domain — ERP IDs, invoice numbers, tenant IDs,
etc.

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

## Marketplace / PSP

Boat marketplace, ride-sharing, e-commerce — buyers and sellers transact
through **internal accounts** (wallet-like balances). Canonical shape:
`examples/marketplace_demo.json` — connection `modern_treasury_bank`, IA refs
like `buyer_maya_wallet`, MT **`name`** fields like *Buyer Maya Payment Account*.

**Do not** add `expected_payment` metadata for "normal" marketplace flows —
EPs are only for **reconciliation** demos. IPDs do not support metadata.

### On legal entities (buyer/seller)
```json
{
    "metadata": {
        "user_type": "seller",
        "marketplace_role": "professional_dealer"
    }
}
```

### On counterparties
```json
{
    "metadata": {
        "user_id": "USR-SELLER-20089",
        "kyc_status": "approved"
    }
}
```

### On internal accounts (sub-accounts / "wallets")
```json
{
    "metadata": {
        "account_purpose": "buyer_sub_account",
        "user_id": "USR-BUYER-10042",
        "linked_listing": "BOAT-2026-0847"
    }
}
```

### On payment orders (book transfers, payouts, ACH collection)

Use `transaction_type` consistently: `marketplace_settlement`,
`platform_fee`, `seller_payout`, and for ACH **debit** pulls that exist to
trigger `sandbox_behavior` returns: `ach_collection` (not `buyer_drawdown`).

```json
{
    "metadata": {
        "listing_id": "BOAT-2026-0847",
        "transaction_type": "marketplace_settlement"
    }
}
```

```json
{
    "metadata": {
        "listing_id": "BOAT-2026-0847",
        "fee_type": "marketplace_commission",
        "fee_rate_pct": "3.0"
    }
}
```

### ACH pull / NSF demo (metadata honesty)

```json
{
    "metadata": {
        "transaction_type": "ach_collection",
        "demo_purpose": "sandbox_auto_return_via_po",
        "sandbox_note": "return simulation requires PO to counterparty; not an IPD"
    }
}
```

---

## Property Management

Rent collection, vendor payouts, lease management.

### On legal entities (tenants, property companies)
```json
{
    "metadata": {
        "tenant_id": "TEN-1001",
        "lease_id": "LEASE-4420",
        "property_id": "PROP-9"
    }
}
```

### On counterparties (vendors)
```json
{
    "metadata": {
        "erp_vendor_id": "VEND-8821",
        "vendor_category": "maintenance"
    }
}
```

### On payment orders (rent collection, vendor payment)
```json
{
    "metadata": {
        "tenant_id": "TEN-1001",
        "lease_id": "LEASE-4420",
        "billing_month": "2026-03",
        "property_id": "PROP-9"
    }
}
```

```json
{
    "metadata": {
        "erp_bill_id": "BILL-9921",
        "cost_center": "MAINT-001",
        "work_order_id": "WO-3310"
    }
}
```

### On virtual accounts (per-tenant collection)
```json
{
    "metadata": {
        "tenant_id": "TEN-1001",
        "property_id": "PROP-9"
    }
}
```

### On expected payments (monthly rent)
```json
{
    "metadata": {
        "tenant_id": "TEN-1001",
        "billing_month": "2026-03",
        "lease_id": "LEASE-4420"
    }
}
```

---

## B2B Accounts Payable / Receivable

Invoice payments, supplier management, ERP integration.

### On counterparties (suppliers)
```json
{
    "metadata": {
        "erp_vendor_id": "VEND-3892",
        "vendor_name": "CloudHost Solutions",
        "payment_terms": "net_30"
    }
}
```

### On payment orders (invoice payments)
```json
{
    "metadata": {
        "invoice_id": "INV-2026-0042",
        "erp_vendor_id": "VEND-3892",
        "purchase_order": "PO-8821",
        "cost_center": "ENG-001",
        "gl_code": "6200"
    }
}
```

### On expected payments (customer receivables)
```json
{
    "metadata": {
        "invoice_id": "INV-2026-0099",
        "customer_id": "CUST-2201",
        "due_date": "2026-04-15"
    }
}
```

### On ledger transactions (journal entries)
```json
{
    "metadata": {
        "journal_entry_id": "JE-2026-0042",
        "source_system": "netsuite",
        "period": "2026-Q1"
    }
}
```

---

## Insurance / Claims

Policy management, claims processing, premium collection.

### On legal entities (policyholders)
```json
{
    "metadata": {
        "policyholder_id": "POL-H-5501",
        "policy_number": "HO-2026-001234"
    }
}
```

### On payment orders (claim payouts, premium refunds)
```json
{
    "metadata": {
        "claim_id": "CLM-2026-0087",
        "policy_id": "HO-2026-001234",
        "claim_type": "property_damage",
        "adjuster_id": "ADJ-201"
    }
}
```

### On expected payments (premium collection)
```json
{
    "metadata": {
        "policy_id": "HO-2026-001234",
        "premium_period": "2026-Q2",
        "premium_type": "quarterly"
    }
}
```

---

## Payroll

Employee payments, tax withholding, benefits.

### On legal entities (employer)
```json
{
    "metadata": {
        "company_id": "COMP-001",
        "payroll_provider": "internal",
        "ein": "12-3456789"
    }
}
```

### On counterparties (employees)
```json
{
    "metadata": {
        "employee_id": "EMP-1042",
        "department": "engineering",
        "pay_schedule": "biweekly"
    }
}
```

### On payment orders (payroll disbursements)
```json
{
    "metadata": {
        "payroll_run_id": "PR-2026-06",
        "employee_id": "EMP-1042",
        "pay_period": "2026-03-01_to_2026-03-15",
        "payment_type": "regular_salary"
    }
}
```

### On payment orders (tax payments)
```json
{
    "metadata": {
        "payroll_run_id": "PR-2026-06",
        "tax_type": "federal_income",
        "tax_period": "2026-Q1"
    }
}
```

---

## Template Variables in Metadata and Descriptions

In Funds Flows, both `metadata` values and `description` fields support
template variables that are resolved per-instance during generation. Use
these to make each generated instance unique.

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
| `{alias_name}` | Actor display name (replace `alias` with frame alias) | `John Doe` |

### Usage in step descriptions

```json
{
    "step_id": "ipd_deposit",
    "type": "incoming_payment_detail",
    "description": "ACH deposit from {first_name} {last_name}",
    "amount": 500000
}
```

### Usage in metadata values

```json
{
    "metadata": {
        "user_id": "USR-{instance}",
        "customer": "{business_name}",
        "region": "{country}"
    }
}
```

### Usage in `trace_value_template`

```json
{
    "trace_value_template": "USR-{instance}",
    "trace_key": "user_id"
}
```

Variables are resolved by `deep_format_map` during generation. Unknown
variables produce empty strings rather than errors, so typos fail silently.
At compile time (before generation), profile variables like `{business_name}`
are not yet available and will be empty.

---

## General Demo Tips

1. **Use realistic IDs** — `INV-2026-0042` is better than `test123`.
2. **Be consistent** — if you use `tenant_id` on one resource, use the same
   key everywhere that tenant appears.
3. **Keep values as strings** — metadata values must be strings, not numbers.
   Use `"250000"` not `250000` for amounts in metadata.
4. **Don't use metadata for loader dependencies** — never put `$ref:` strings
   in metadata. Use `depends_on` for ordering and data fields for structural
   references.
5. **Include at least 2-3 keys** — one key is sparse, five is cluttered.
   Two or three keys per resource is the sweet spot for demos.
