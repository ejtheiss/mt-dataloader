# Decision Rubrics: When to Use Each Modern Treasury Resource

This document tells you which MT resource type to use for a given business
intent. Every resource listed here maps to a top-level section in the
DataLoaderConfig JSON.

---

## Default: PSP / Marketplace (wallets, no ledger)

For **payment service provider** and **marketplace** demos where users hold
funds in **internal accounts as wallets**:

- **Use:** `legal_entity`, `counterparty` (with inline accounts +
  `sandbox_behavior`), `internal_account` (per user + platform revenue),
  `payment_order` (`book` for wallet-to-wallet / fees, `ach` for bank payout
  or ACH collection).
- **Inbound buyer funds (sandbox):** `incoming_payment_detail` simulates an
  external **push** into a wallet — not something you “do” in production the
  same way; it is sandbox simulation.
- **Do not add by default:** `ledger`, `ledger_account`, `ledger_transaction`,
  `virtual_account`, `expected_payment` — only if the demo is explicitly about
  accounting or reconciliation attribution.
- **NSF / auto-return via magic account:** only works on **POs to a
  counterparty account** (`sandbox_behavior: "return"`). That is an **ACH
  debit pull** (or credit to a return-test account), not an IPD. Label it
  that way in descriptions and metadata.

---

## Connections

A connection represents a link to a banking partner. In sandbox, connections
can be created via the config. In production, they are provisioned by MT.

| Intent | Config section | Key fields |
|--------|---------------|------------|
| Link to a banking partner (sandbox) | `connections` | `entity_id` (one of `example1`, `example2`, `modern_treasury`), `nickname` |

Every config that creates internal accounts needs at least one connection.
Connections cannot be deleted.

---

## Legal Entities

A legal entity is a person or business. Required for KYC/KYB onboarding.

| Intent | `legal_entity_type` | Required fields |
|--------|-------------------|-----------------|
| Represent a business | `business` | `business_name`, `date_formed`, `legal_structure`, `country_of_incorporation`, `identifications` (at least one, e.g. `us_ein`), `addresses` (with `address_types`) |
| Represent an individual | `individual` | `first_name`, `last_name`, `date_of_birth`, `citizenship_country`, `identifications` (at least one, e.g. `us_ssn`), `addresses` |

Legal entities cannot be deleted. Always include `identifications` — the MT
API will reject businesses without a tax ID and individuals without an SSN
or equivalent.

`legal_structure` values: `corporation`, `llc`, `non_profit`, `partnership`,
`sole_proprietorship`, `trust`.

---

## Counterparties

A counterparty is an external party you transact with. Counterparties carry
inline external accounts (bank info).

| Intent | Config section | Key fields |
|--------|---------------|------------|
| External party with bank account | `counterparties` | `name`, `accounts[]` (inline bank accounts), optional `legal_entity_id` |

### Inline accounts on counterparties

Each counterparty can have one or more `accounts[]`. These are created
inline with the counterparty and auto-registered as child refs:

- `$ref:counterparty.<key>.account[0]` — first account
- `$ref:counterparty.<key>.account[1]` — second account (if present)

### Sandbox behavior (critical for demos)

Set `sandbox_behavior` on the account to control how the sandbox processes
payments sent to this counterparty:

| `sandbox_behavior` | Effect | Magic account number |
|-------------------|--------|---------------------|
| `success` | Payment completes normally | `123456789` |
| `return` | Payment auto-returns with the specified ACH code | `100XX` (where XX = return code digits) |
| `failure` | Payment fails outright | `1111111110` |

When `sandbox_behavior` is set, `account_details` and `routing_details`
are auto-populated — you do not need to specify them. Set
`sandbox_return_code` alongside `return` (e.g. `"R01"` for NSF).

**When the config includes `counterparties`, set `sandbox_behavior` on every
inline account used for outbound PO demos.** Without it, the sandbox may use
unpredictable outcomes. Configs with **no** counterparties (e.g. internal-only
`book` transfers) do not need this.

---

## Internal Accounts

An internal account is a bank account owned by the platform. In PSP/marketplace
models, each user gets their own internal account as a "wallet."

| Intent | Config section | Key fields |
|--------|---------------|------------|
| Platform operating account | `internal_accounts` | `connection_id`, `name`, `party_name`, `currency` (USD or CAD) |
| Per-user wallet (PSP/marketplace) | `internal_accounts` | Same, plus `legal_entity_id` to link to the user's LE |
| Platform revenue/fee account | `internal_accounts` | Same, named for the fee purpose |

Internal accounts cannot be deleted. They require a `connection_id` ref.
A child ref `$ref:internal_account.<key>.ledger_account` is auto-registered
if the banking partner auto-creates a ledger account.

---

## External Accounts

A standalone external account attached to an existing counterparty. Use this
when you need to add a *second* bank account to a counterparty that was
already created, or when you need an account with a ledger account attached.

| Intent | Config section | Key fields |
|--------|---------------|------------|
| Additional bank account on existing counterparty | `external_accounts` | `counterparty_id`, `account_details`, `routing_details` |
| Bank account with inline ledger account | `external_accounts` | Same, plus `ledger_account` (inline) |

Most demos use inline `accounts[]` on the counterparty instead. Use
`external_accounts` only when you specifically need a standalone account
or an inline ledger account.

---

## Virtual Accounts (Rare)

Use **sparingly.** Most PSP and marketplace demos should use **internal
accounts as wallets** only.

A virtual account is a sub-account on an IA for **per-payer inbound
attribution** on the **same** real bank account — not a separate wallet
balance.

| Intent | Config section | Key fields |
|--------|---------------|------------|
| Per-payer inbound routing label | `virtual_accounts` | `name`, `internal_account_id`, optional `counterparty_id` |

**Do not** use virtual accounts for PSP wallet balances — use internal
accounts. Only add a VA when the demo story is specifically about VA-based
inbound attribution (uncommon).

---

## Payment Orders

A payment order moves money. This is the most common resource in demos.

| Intent | `type` | `direction` | Accounts |
|--------|--------|------------|----------|
| Pay a vendor / supplier | `ach`, `wire`, or `rtp` | `credit` | `originating_account_id` = internal account, `receiving_account_id` = counterparty account |
| Collect from a customer (drawdown) | `ach` | `debit` | `originating_account_id` = internal account, `receiving_account_id` = counterparty account |
| Move funds between wallets (book) | `book` | `credit` | Both are `internal_account` refs |
| Collect platform fee | `book` | `credit` | From user wallet IA to platform revenue IA |
| Payout to external bank | `ach`, `wire` | `credit` | From user IA to counterparty external account |

**Rules:**
- `direction: credit` requires `receiving_account_id`
- `direction: debit` — `receiving_account_id` is the source being debited
- `amount` is in cents (e.g. 10000 = $100.00)
- `type: book` = internal transfer between two internal accounts. Always `direction: credit`.
- Inline `ledger_transaction` can be attached for double-entry accounting

Payment orders cannot be deleted.

---

## Expected Payments (Reconciliation Only)

An **expected payment** is a **reconciliation matcher** — it does **not**
move money. MT matches incoming items (e.g. IPDs) to EP rules.

| Intent | Config section | Key fields |
|--------|---------------|------------|
| Reconciliation / “we expect this inbound” | `expected_payments` | `reconciliation_rule_variables` (required), `description` |

**PSP / marketplace:** Do **not** add EPs unless the demo explicitly shows
reconciliation status or matching. Sandbox IPD `create_async()` completes
without needing an EP for the money to appear; an EP you never surface in the
UI adds noise.

If you **do** demo reconciliation: create the EP **before** the IPD in the
DAG (e.g. IPD `depends_on` the EP), and use matching amounts on the same IA.

`reconciliation_rule_variables` must include `internal_account_id`,
`direction`, `amount_lower_bound`, `amount_upper_bound`, and `type`.

---

## Incoming Payment Details (Sandbox Simulation)

**Production:** IPDs are created by the **bank** when money arrives — you
receive them; you do not originate them like a PO.

**Sandbox:** `incoming_payment_detail` + `create_async()` **simulates** an
external party sending money **into** an internal account (e.g. buyer push
to wallet). Treat it as **inbound deposit simulation**, not a generic
“workflow step” interchangeable with payment orders.

| Intent | Config section | Key fields |
|--------|---------------|------------|
| Simulate inbound credit to an IA | `incoming_payment_details` | `type`, `direction`, `amount`, `internal_account_id` |

After creation, the loader polls until `completed`. Child refs may include
`transaction` (and ledger linkage if your org uses it).

**IPDs do not support metadata.**

Downstream **book** transfers that spend those funds should `depends_on` the
IPD because their PO fields only reference IAs, not the IPD.

**Failures on inbound:** `sandbox_behavior` does **not** apply to IPDs. Use
an explicit `return` with `returnable_id` pointing at the IPD if you need
an inbound return story.

---

## Ledgers & Ledger Accounts (Skip for PSP-Only Demos)

Double-entry bookkeeping. Omit entirely for **PSP / marketplace wallet**
configs unless the customer asked for ledgering.

| Intent | Config section | Key fields |
|--------|---------------|------------|
| Create a ledger | `ledgers` | `name`, `description` |
| Create a ledger account | `ledger_accounts` | `name`, `ledger_id`, `normal_balance` (credit or debit), `currency` |
| Standalone ledger transaction | `ledger_transactions` | `ledger_entries[]` (at least one debit + one credit, must balance) |
| Inline ledger transaction on PO | `ledger_transaction` field on `payment_orders` | Same `ledger_entries` structure |

**Normal balance conventions:**
- Assets (Cash, AR): `debit`
- Liabilities (AP): `credit`
- Revenue: `credit`
- Expenses/Refunds: `debit`

Ledger transactions can be archived during cleanup but not deleted.

---

## Ledger Account Categories

Organizational grouping for ledger accounts (e.g. "Assets", "Liabilities").

| Intent | Config section | Key fields |
|--------|---------------|------------|
| Create a category | `ledger_account_categories` | `name`, `ledger_id`, `normal_balance`, `currency` |
| Add account to category | `category_memberships` | `category_id`, `ledger_account_id` |
| Nest categories | `nested_categories` | `parent_category_id`, `sub_category_id` |

---

## Returns

Return an **incoming payment detail** (inbound item). **`sandbox_behavior`
does not apply to IPDs** — only to POs sent to counterparty accounts.

| Intent | Config section | Key fields |
|--------|---------------|------------|
| Return / bounce an inbound IPD | `returns` | `returnable_id` → IPD, optional `code`, `reason` |

**Returns do not support metadata.**

For **outbound** PO failures / NSF demos to a buyer’s bank, use
`sandbox_behavior: "return"` on the counterparty **and** an ACH PO — that is
not the same as an IPD return.

---

## Reversals

Reverse a completed/sent payment order. The PO must reach `approved`,
`sent`, or `completed` status before a reversal can be created.

| Intent | Config section | Key fields |
|--------|---------------|------------|
| Reverse a payment order | `reversals` | `payment_order_id`, `reason` |

`reason` values: `duplicate`, `incorrect_amount`,
`incorrect_receiving_account`, `date_earlier_than_intended`,
`date_later_than_intended`.

The handler automatically polls the PO status until it reaches a reversible
state (up to 60s). Not all sandbox connections support reversals.

---

## Cleanup / Deletability Reference

| Resource | Can be deleted? | Cleanup behavior |
|----------|----------------|-----------------|
| connection | No | Skipped |
| legal_entity | No | Skipped |
| internal_account | No | Skipped |
| payment_order | No | Skipped |
| incoming_payment_detail | No | Skipped |
| return | No | Skipped |
| reversal | No | Skipped |
| ledger_transaction | No (archived) | Archived |
| counterparty | **Yes** | Deleted |
| external_account | **Yes** | Deleted |
| virtual_account | **Yes** | Deleted |
| ledger | **Yes** | Deleted |
| ledger_account | **Yes** | Deleted |
| ledger_account_category | **Yes** | Deleted |
| expected_payment | **Yes** | Deleted |
| category_membership | **Yes** | Removed |
| nested_category | **Yes** | Removed |

Plan configs knowing that non-deletable resources (LEs, IAs, POs, IPDs)
will persist in the sandbox org after cleanup.
