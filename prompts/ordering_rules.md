# Ordering Rules: How Resources Are Sequenced

The dataloader builds a directed acyclic graph (DAG) from the config and
executes resources in topological order. Within each batch, resources with
no dependencies between them run concurrently.

You almost never need to think about ordering — the engine handles it. This
document explains the rare cases where you do.

---

## Automatic Ordering (Default — No Action Required)

Any `$ref:` in a data field automatically creates a DAG edge. The engine
scans field values on each resource for `$ref:` strings and adds dependency
edges.

**Child ref expansion:** If you reference a child ref like
`$ref:counterparty.vendor_bob.account[0]`, the engine automatically adds
an edge to the parent `counterparty.vendor_bob` too.

---

## When `depends_on` Is Needed

`depends_on` is for **business timing** — when a resource must wait for
another resource that it does NOT reference in any data field.

The classic PSP pattern: **after an inbound deposit (simulated IPD), book
transfers move those funds** — the POs only reference internal accounts, so
they need an explicit edge to the IPD.

```json
{
    "ref": "po_settle_to_seller",
    "type": "book",
    "direction": "credit",
    "originating_account_id": "$ref:internal_account.buyer_wallet",
    "receiving_account_id": "$ref:internal_account.seller_wallet",
    "depends_on": ["$ref:incoming_payment_detail.ipd_buyer_deposit"]
}
```

---

## Sandbox Simulations (IPDs)

`incoming_payment_detail` simulates **inbound money from outside** landing in
an internal account.

- **Production:** The bank creates IPDs when external payments arrive — you
  do not create them; they are *events you receive*.
- **Sandbox:** `create_async()` is **simulation plumbing** — it fakes an
  external party sending money to your IA so you can demo inbound flows
  without a real bank.

An IPD is **not** a “platform action” in the same sense as a payment order.
Do not describe configs as “doing an IPD step” in business language — say
**inbound deposit (simulated via IPD in sandbox)**, then **settlement** via
book transfers.

**Deposit → settlement chain (PSP / marketplace):**

```
incoming_payment_detail.buyer_deposit   ← simulates external push to wallet
    → payment_order.settle_to_seller    ← book: net to seller (depends_on IPD)
    → payment_order.platform_fee        ← book: fee (often depends_on settle first)
        → payment_order.seller_payout   ← ACH: to seller bank (depends_on settle)
```

Use `depends_on` on book transfers (and fee/payout ordering) because POs
do not reference the IPD in their account fields.

---

## Sandbox Auto-Returns (`sandbox_behavior`)

On a counterparty’s **inline** account, `sandbox_behavior` controls outcomes
for **Payment Orders sent to that external account**:

| Value | Effect |
|-------|--------|
| `success` | PO completes normally |
| `return` | PO auto-returns (use `sandbox_return_code`, e.g. `R01`) |
| `failure` | PO fails outright |

**Critical:** `sandbox_behavior` applies to **outbound POs to the
counterparty’s bank account**. It does **not** apply to IPDs (`create_async`).
Inbound deposit failures are modeled with an explicit `return` resource
against the IPD, not with `sandbox_behavior` on the counterparty.

If you need an **ACH pull** that auto-returns (e.g. NSF on a debit), use
`direction: "debit"` with `sandbox_behavior: "return"` on the **buyer’s**
counterparty account, and describe it honestly as **platform ACH collection /
pull** — not as the same thing as a buyer-push deposit simulated by IPD.

---

## Common Patterns Requiring `depends_on`

### 1. Inbound deposit before settlement (PSP)

After a simulated deposit (IPD) credits a buyer wallet, book transfers that
move those funds must `depends_on` the IPD.

### 2. Fee or payout after a prior book transfer

When two debits from the same wallet must not race, sequence them (e.g.
`po_platform_fee` `depends_on` `po_settle_to_seller`).

### 3. Payout after settlement

Seller ACH payout `depends_on` the book transfer that funded the seller wallet.

### 4. Explicit return after IPD

A `return` references `returnable_id` → IPD; the DAG edge is automatic.
Add `depends_on` to the return only if you need extra ordering beyond that.

---

## What NOT to Do

- **Redundant `depends_on`** for refs already in data fields.
- **Circular dependencies** — `CycleError` at dry run.
- **Expected payments in PSP demos “by default”** — EPs are reconciliation
  matchers only; they do not move money. For marketplace/PSP flows that are
  not explicitly about reconciliation UI, omit EPs (see IPD/EP review).

---

## Execution Order Summary (Typical)

Rough layering — actual batches come from the DAG:

1. **Connections**
2. **Legal entities, ledgers** (PSP marketplace demos often have **no ledgers**)
3. **Counterparties, ledger accounts**
4. **Internal accounts, external accounts, ledger categories**
5. **Virtual accounts, expected payments, payment orders** — VAs and EPs are
   **uncommon** in PSP wallet demos; omit unless the story needs them.
6. **Incoming payment details** — sandbox simulation of inbound funds; not
   interchangeable with “creating a normal business step” in copy.
7. **Ledger transactions, returns, reversals**
8. **Category memberships, nested categories**

Within a batch, resources run concurrently up to `max_concurrent_requests`.
