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
  external **push** into a wallet — not something you "do" in production the
  same way; it is sandbox simulation.
- **Do not add by default:** `ledger`, `ledger_account`, `ledger_transaction`,
  `virtual_account`, `expected_payment` — only if the demo is explicitly about
  accounting or reconciliation attribution.
- **NSF / return demos — two patterns exist:**
  1. **Outbound PO return** — `sandbox_behavior: "return"` on the
     counterparty's account triggers an auto-return on POs sent to that
     account. Good for ACH debit/credit return demos.
  2. **Inbound IPD return** — a `return` step (or standalone `return`
     resource) with `returnable_id` pointing at an IPD. Used in
     `funds_flows` `optional_groups` for inbound deposit NSF stories.
  `sandbox_behavior` does **not** apply to IPDs; always use an explicit
  `return` resource/step for IPD returns.

---

## Connections

A connection represents a link to a banking partner. In sandbox, connections
can be created via the config. In production, they are provisioned by MT.

| Intent | Config section | Key fields |
|--------|---------------|------------|
| Link to a banking partner (sandbox) | `connections` | `entity_id` (one of `example1`, `example2`, `modern_treasury`), `nickname` |

### Connection capabilities (sandbox)

| `entity_id` | ACH credit | ACH debit | Book | Wire | Notes |
|--------------|:----------:|:---------:|:----:|:----:|-------|
| `example1` | Yes | Yes | Yes | Yes | **Recommended for demos.** Full payment capabilities on newly created IAs. |
| `example2` | Yes | Yes | Yes | Yes | Same as `example1`. |
| `modern_treasury` | Limited | **No** | Yes | No | Newly created IAs only support `book` transfers. Do **not** use for configs that need ACH or wire POs. |

**Always use `example1`** (or `example2`) when the demo includes ACH payment
orders, ACH debit pulls, wire transfers, or any non-book payment type.
Use `modern_treasury` only for configs that exclusively use `book` transfers.

Every config that creates internal accounts needs at least one connection.
Connections cannot be deleted.

---

## Legal Entities

A legal entity is a person or business. Required for KYC/KYB onboarding.

### Compliance fields are fully managed by the dataloader

The dataloader **always overwrites** `identifications`, `addresses`, and
`documents` with sandbox-safe mock data. Any values you provide for these
fields are **silently replaced** — so **never include them** in the JSON.

| `legal_entity_type` | Fields you provide | Auto-managed (do NOT include) |
|---------------------|--------------------|-------------------------------|
| `business` | `ref`, `legal_entity_type`, `business_name`, optional `legal_structure`, optional `metadata` | `identifications`, `addresses`, `documents`, `date_formed`, `country_of_incorporation` |
| `individual` | `ref`, `legal_entity_type`, `first_name`, `last_name`, optional `email`, optional `metadata` | `identifications`, `addresses`, `documents`, `date_of_birth`, `citizenship_country` |

```json
{
    "ref": "acme_corp",
    "legal_entity_type": "business",
    "business_name": "Acme Corp",
    "metadata": { "platform_role": "marketplace_operator" }
}
```

```json
{
    "ref": "buyer_alice",
    "legal_entity_type": "individual",
    "first_name": "Alice",
    "last_name": "Jones",
    "metadata": { "user_type": "buyer" }
}
```

The mock provides: EIN/SSN, passport (for individuals), US address, required
documents (`articles_of_incorporation` for businesses), and default dates/structure.

Legal entities cannot be deleted.

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

**Allowed fields** on each `accounts[]` object include: `account_type`,
`party_name`, `party_type`, `party_address`, `account_details`, `routing_details`,
`metadata`, plus sandbox-only `sandbox_behavior` and `sandbox_return_code`.

**Not allowed:** `name` on the inline account (schema uses `extra="forbid"`).
The counterparty itself has `name`; for an account-level label use `party_name`
or a string in `metadata` (e.g. `account_label`).

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
| Platform operating account | `internal_accounts` | `connection_id`, `name`, `party_name`, `currency`, `legal_entity_id` |
| Per-user wallet (PSP/marketplace) | `internal_accounts` | Same — `legal_entity_id` links to the user's LE |
| Platform revenue/fee account | `internal_accounts` | Same — `legal_entity_id` links to the **platform's** LE |

**Every internal account MUST have a `legal_entity_id`.** The Modern Treasury
connection requires it. For platform-owned accounts (revenue, operating, fee),
reference the platform's own legal entity.

Supported `currency` values: `USD`, `CAD`, `USDC`, `USDG`. Use `USD` unless
the demo explicitly involves stablecoin rails (`USDG`, `USDC`) or Canadian
dollar flows (`CAD`).

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

### Staged payment orders

Set `staged: true` on a PO to defer its API creation until the presenter
clicks "Fire" in the run-detail UI. Typical use: the settlement / fee /
payout chain that you want to trigger live during a demo.

A staged PO **may** reference non-staged resources (IAs, CPs) — their IDs
are resolved during the normal run. A staged PO must **not** use data-field
`$ref:` to another staged resource; use `depends_on` for ordering.

---

## Expected Payments (Reconciliation Only)

An **expected payment** is a **reconciliation matcher** — it does **not**
move money. MT matches incoming items (e.g. IPDs) to EP rules.

| Intent | Config section | Key fields |
|--------|---------------|------------|
| Reconciliation / "we expect this inbound" | `expected_payments` | `reconciliation_rule_variables` (required), `description` |

**PSP / marketplace:** Do **not** add EPs unless the demo explicitly shows
reconciliation status or matching. Sandbox IPD `create_async()` completes
without needing an EP for the money to appear; an EP you never surface in the
UI adds noise.

If you **do** demo reconciliation: create the EP **before** the IPD in the
DAG (e.g. IPD `depends_on` the EP), and use matching amounts on the same IA.

`reconciliation_rule_variables` must include `internal_account_id`,
`direction`, `amount_lower_bound`, `amount_upper_bound`, and `type`.

### Staged expected payments

`staged: true` on an EP defers creation until the presenter fires it. Use
this when the demo story involves creating the reconciliation matcher live,
then showing an IPD being matched after.

---

## Incoming Payment Details (Sandbox Simulation)

**Production:** IPDs are created by the **bank** when money arrives — you
receive them; you do not originate them like a PO.

**Sandbox:** `incoming_payment_detail` + `create_async()` **simulates** an
external party sending money **into** an internal account (e.g. buyer push
to wallet). Treat it as **inbound deposit simulation**, not a generic
"workflow step" interchangeable with payment orders.

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

### Staged incoming payment details

`staged: true` on an IPD defers its `create_async()` call until the
presenter fires it. The "Fire" action creates the IPD and **polls** until it
reaches a terminal state (`completed`, `returned`, or `failed`).

Staged IPDs are the classic entry point for a live demo chain: fire the
inbound deposit, then fire downstream book transfers and payouts one by one.

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

When a PO includes an inline `ledger_transaction`, the created ledger
transaction ID is auto-registered as a child ref:
`$ref:payment_order.<key>.ledger_transaction`. Downstream resources can
reference it directly.

**Normal balance conventions:**
- Assets (Cash, AR): `debit`
- Liabilities (AP): `credit`
- Revenue: `credit`
- Expenses/Refunds: `debit`

Ledger transactions can be archived during cleanup but not deleted.

### `ledger_entries[]` payload shape

Each entry in `ledger_entries` has exactly three fields: `amount` (cents),
`direction` (`"credit"` or `"debit"`), and `ledger_account_id` (`$ref:` to a
ledger account). The sum of all debit amounts must equal the sum of all credit
amounts (balanced double-entry).

**Standalone ledger transaction (simple 2-leg):**

```json
{
    "ref": "lt_seed_alice_wallet",
    "description": "Initial USD funding for Alice wallet",
    "ledger_entries": [
        {
            "amount": 100000,
            "direction": "debit",
            "ledger_account_id": "$ref:ledger_account.platform_cash_usd"
        },
        {
            "amount": 100000,
            "direction": "credit",
            "ledger_account_id": "$ref:ledger_account.alice_usd_wallet"
        }
    ],
    "metadata": {
        "journal_entry_id": "JE-001",
        "source_system": "platform"
    }
}
```

**Standalone ledger transaction (4-leg reallocation):**

```json
{
    "ref": "lt_alice_usd_to_usdg",
    "description": "USD to USDG reallocation for Alice",
    "ledger_entries": [
        {
            "amount": 20000,
            "direction": "credit",
            "ledger_account_id": "$ref:ledger_account.platform_cash_usd"
        },
        {
            "amount": 20000,
            "direction": "debit",
            "ledger_account_id": "$ref:ledger_account.platform_usdg_reserve"
        },
        {
            "amount": 20000,
            "direction": "debit",
            "ledger_account_id": "$ref:ledger_account.alice_usd_wallet"
        },
        {
            "amount": 20000,
            "direction": "credit",
            "ledger_account_id": "$ref:ledger_account.alice_usdg_wallet"
        }
    ],
    "depends_on": ["$ref:incoming_payment_detail.ipd_alice_funding"]
}
```

**Inline ledger transaction on a payment order:**

```json
{
    "ref": "po_platform_fee",
    "type": "book",
    "direction": "credit",
    "amount": 500,
    "originating_account_id": "$ref:internal_account.buyer_wallet",
    "receiving_account_id": "$ref:internal_account.platform_revenue",
    "ledger_transaction": {
        "ledger_entries": [
            {
                "amount": 500,
                "direction": "debit",
                "ledger_account_id": "$ref:ledger_account.buyer_wallet_liability"
            },
            {
                "amount": 500,
                "direction": "credit",
                "ledger_account_id": "$ref:ledger_account.revenue"
            }
        ],
        "description": "Platform fee journal entry"
    }
}
```

**Rules:**
- Minimum one entry, but practically always at least one debit + one credit.
- Total debit amounts must equal total credit amounts.
- `amount` is in cents (same as payment orders).
- `ledger_account_id` uses `$ref:ledger_account.<ref>` syntax.
- Optional fields on both standalone and inline: `description`,
  `effective_at`, `effective_date`, `external_id`, `status`
  (`"archived"`, `"pending"`, or `"posted"`).
- Standalone ledger transactions also support `ledgerable_type` and
  `ledgerable_id` for linking to a parent resource, and `metadata`.
- Inline ledger transactions on POs support `metadata`.

### Staged ledger transactions

`staged: true` on a standalone ledger transaction defers its creation.
Use when the demo story involves recording journal entries live (e.g.
revenue recognition after a payment completes).

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

For **outbound** PO failures / NSF demos to a buyer's bank, use
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

## Staged Resources (Cross-Cutting)

Four resource types support `staged: true`: **payment_order**,
**incoming_payment_detail**, **expected_payment**, and
**ledger_transaction**. Staged resources are skipped during the normal run
and appear as "Fire" buttons in the run-detail UI.

**When to use staged:**
- The demo involves a presenter clicking through a money-movement story
  step by step (deposit → fee → settlement → payout).
- You want to show webhook events arriving in real time after each action.

**When NOT to use staged:**
- The demo is non-interactive (batch creation, overnight job simulation).
- The resource is a prerequisite for other non-staged resources.

**Dependency rules (enforced by the validator):**
1. Non-staged resources must **never** depend on staged resources.
2. Staged resources **may** depend on non-staged resources (IDs resolve
   normally).
3. Staged resources must **not** have data-field `$ref:` to other staged
   resources (IDs don't exist yet). Use `depends_on` for ordering.

**Typical staged chain (PSP marketplace):**
```
ipd_buyer_deposit (staged) → po_platform_fee (staged, depends_on IPD)
                            → po_settle_seller (staged, depends_on fee)
                            → po_payout_seller (staged, depends_on settle)
```

See `examples/staged_demo.json` for a full working example.

---

## Cleanup / Deletability Reference

| Resource | Can be deleted? | Cleanup behavior |
|----------|----------------|-----------------|
| connection | No | Skipped |
| legal_entity | No | Skipped |
| internal_account | No | Request closure via `archive_resource` step |
| payment_order | No | Skipped |
| incoming_payment_detail | No | Skipped |
| return | No | Skipped |
| reversal | No | Skipped |
| ledger_transaction | No (archived) | Archived |
| transition_ledger_transaction | No | Updates existing LT status |
| counterparty | **Yes** | Deleted |
| external_account | **Yes** | Deleted |
| virtual_account | **Yes** | Deleted |
| ledger | **Yes** | Deleted |
| ledger_account | **Yes** | Deleted |
| ledger_account_category | **Yes** | Deleted |
| ledger_account_settlement | **Yes** | Deleted |
| ledger_account_balance_monitor | **Yes** | Deleted |
| ledger_account_statement | No | Skipped |
| legal_entity_association | No | Skipped |
| expected_payment | **Yes** | Deleted |
| category_membership | **Yes** | Removed |
| nested_category | **Yes** | Removed |

Plan configs knowing that non-deletable resources (LEs, IAs, POs, IPDs)
will persist in the sandbox org after cleanup.

---

## Funds Flows (lifecycle patterns)

When the demo involves a multi-step payment lifecycle, use the `funds_flows`
DSL section. This replaces manually building individual `payment_orders`,
`incoming_payment_details`, `ledger_transactions`, `returns`, and
`reversals` sections.

**Use funds_flows when:**
- The demo involves 2+ related payment/ledger steps
- The SE wants to visualize the money flow
- The SE will scale the pattern ("generate 100 of these")
- The demo involves lifecycle variants (returns, reversals, alternative payout methods)
- The demo involves per-user infrastructure (use `instance_resources` to create them)
- The demo needs ledger transaction lifecycle (pending → posted → archived
  via `transition_ledger_transaction` steps)

**Use `instance_resources` when:**
- Each flow instance needs to **create** its own legal entity, counterparty, internal account, or ledger account
- The SE wants to scale from 1 user to 100+ users with unique names and accounts
- Use `{first_name}`, `{last_name}`, `{business_name}`, `{instance}` placeholders
- Seed profiles are pulled from the selected dataset (standard, industry verticals, pop-culture)
- Note: `{instance}` placeholders work in **all** flows (actor slots, refs, descriptions) — `instance_resources` is only needed when the flow must **define** the resources, not just reference them

**Use raw resource arrays when:**
- Single isolated resources (one PO, one LT)
- Complex non-linear patterns that don't fit the step model
- Configs that mix funds_flows with additional standalone resources
