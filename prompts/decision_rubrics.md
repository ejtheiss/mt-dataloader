# Decision Rubrics: When to Use Each Modern Treasury Resource

Which **Modern Treasury resource** to use for each intent, and how it appears in config.

**Authoring rule:** Do **not** hand-write lifecycle resources in these
top-level arrays (`payment_orders`, `incoming_payment_details`,
`expected_payments`, `ledger_transactions`, `returns`, `reversals`,
`transition_ledger_transactions`, `verify_external_accounts`,
`complete_verifications`, `archive_resources`). Express
them only as `**funds_flows[].steps`** (and `optional_groups`). The compiler
expands steps into the sections this document names and re-validates the flat
`DataLoaderConfig` — including `**verify_external_account`**, `**complete_verification**`, and `**archive_resource**` (same pipeline as payment orders, not “schema-only” step types).

### Root JSON: which step types have a top-level array

**The only lifecycle sections on authored / validated `DataLoaderConfig` are
the ones defined by the schema** (see `GET /api/schema`). **Do not invent root
keys** from step type names.


| Step `type`                     | Top-level `DataLoaderConfig` key (if any)                                                                                                                       |
| ------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `payment_order`                 | `payment_orders` (compiler emit; do not hand-author)                                                                                                            |
| `incoming_payment_detail`       | `incoming_payment_details`                                                                                                                                      |
| `expected_payment`              | `expected_payments`                                                                                                                                             |
| `ledger_transaction`            | `ledger_transactions`                                                                                                                                           |
| `return`                        | `returns`                                                                                                                                                       |
| `reversal`                      | `reversals`                                                                                                                                                     |
| `transition_ledger_transaction` | `transition_ledger_transactions`                                                                                                                                |
| `verify_external_account`       | `**verify_external_accounts`** after compile only. Author as a `**funds_flows`** step; do **not** hand-write the root array (same pattern as `payment_orders`). |
| `complete_verification`         | `**complete_verifications`** after compile only. Author as a `**funds_flows`** step; do **not** hand-write the root array.                                      |
| `archive_resource`              | `**archive_resources`** after compile only. Author as a `**funds_flows`** step; do **not** hand-write the root array.                                           |


**Flat vs authoring:** `verify_external_accounts`, `complete_verifications`, and `archive_resources` are real top-level fields on `DataLoaderConfig` (so compile + validation succeed end-to-end). **Author** them as `**funds_flows`** steps; treat hand-written root rows as **compiled output** you are editing, not something to invent from scratch.

**Do not infer** a root section name by pluralizing a step type. If the key is
not in the schema, it is `**extra_forbidden`**.

---

## Default: PSP / Marketplace (wallets, no ledger)

For **payment service provider** and **marketplace** demos where users hold
funds in **internal accounts as wallets**:

- **Use:** `**funds_flows`** with `**user_N` + `direct_N` actors**; **platform**
`legal_entity`, `internal_account`, and shared infra as **top-level** static
rows; **each distinct payor/payee (or other `user` party)** via `**instance_resources`**
on that flow (`{instance}` in refs, placeholders in names) — see `**system_prompt.md` → *User actors (mandatory JSON)***.
Counterparties for variable `user` parties belong in that flow’s `instance_resources*`*. Top-level `**counterparty`** rows are only for platform / `direct_N` / explicitly fixed parties. `**sandbox_behavior` on inline CP bank
accounts used for PO demos. Express `payment_order` and `incoming_payment_detail` as
`**funds_flows` steps**, not as hand-written top-level arrays.
- **Inbound buyer funds (sandbox):** an **IPD step** simulates an external
**push** into a wallet — not something you "do" in production the same way;
it is sandbox simulation.
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


| Intent                              | Config section | Key fields                                                                 |
| ----------------------------------- | -------------- | -------------------------------------------------------------------------- |
| Link to a banking partner (sandbox) | `connections`  | `entity_id` (one of `modern_treasury`, `example1`, `example2`), `nickname` |


### Default (nearly always): `modern_treasury`

**Use `entity_id: "modern_treasury"`** for standard PSP / marketplace / funds-flow
demos, wallet stories, book + ACH-style configs, and anything that is **not**
explicitly a **Bring Your Own Bank (BYOB)** sandbox exercise.

**PSP sandbox capabilities:** `modern_treasury` is the **platform PSP sandbox** connection. It supports **ACH**, **wire**, **RTP**, **book**, **USD**, and **stablecoins** (e.g. **USDC**, **USDG**, **PYUSD**, **USDT**—align names with current MT sandbox product docs). **Do not** tell authors that this connection is “book-only” or that ACH/wire **requires** `example1` / `example2`; those entities are for **BYOB** simulation personalities, not a workaround for missing PSP rails.

Use a clear `ref` (e.g. `platform_bank`, `mt_sandbox`) and a human nickname
(e.g. `"Modern Treasury PSP"`). This should be the **default** unless the user
answered **yes** to the BYOB question (see below).

### One `modern_treasury` connection, many internal accounts (default)

**Default PSP demos** use **one** `connections[]` row with `**entity_id: "modern_treasury"`** and a clear `ref` + nickname. Hang **all** internal accounts
that belong to that PSP on the **same** `connection_id` (`$ref:connection.<ref>`)
— whether the IA is **USD**, **CAD**, **USDC**, **USDG**, or used for **book**,
**ACH**, **stablecoin** payment orders, etc. Currency and payment type are
properties of the **internal account** and the **payment order**, not separate
connection rows per currency.

Use `**examples/stablecoin_ramp.json`** as a funds-flow template: one shared PSP
connection, USD + USDC internal accounts, ledger structure, and optional-group
payout alternatives.

Add a **second** connection only when the story truly needs another banking
partner (e.g. **BYOB** `example1` / `example2`, or a doc-driven multi-bank
setup)—not “one connection per currency.”

### BYOB only: `example1` and `example2`

Reserve `**example1`** and `**example2`** for configs that intentionally model
**[Building in Sandbox (Bring Your Own Bank)](https://docs.moderntreasury.com/payments/docs/building-in-sandbox-bring-your-own-bank)**:
fake bank behaviors (Gringotts vs Iron Bank), reconciliation-rule drills, or
docs-accurate simulation patterns. **Do not** use `example1` / `example2` for
generic demos if the user did not ask for BYOB.


| `entity_id`       | Sandbox role (BYOB)                                                    | When to use in config                                                                                                                                                                                                                            |
| ----------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `example1`        | **Gringotts Wizarding Bank (GWB)** — create with nickname e.g. `"GWB"` | Payment orders **reach the bank**; transactions appear shortly after; **expected payments** on GWB IAs get an **auto-created transaction** (~10s) and reconcile automatically. Good for **end-to-end PO + webhook + fast settlement** stories.   |
| `example2`        | **Iron Bank of Braavos (IBB)** — create with nickname e.g. `"IBB"`     | **Does not process payment orders.** **Expected payments** on IBB IAs reconcile only when **you** supply a matching **transaction** (reconciliation rules). Good for **reconciliation rules, manual/imported transactions, EP-matching** drills. |
| `modern_treasury` | Default MT sandbox PSP connection                                      | **Default** for normal dataloader demos.                                                                                                                                                                                                         |


**Before** generating a BYOB config, confirm with the user (or infer clearly
from their ask) and run through **BYOB clarifying questions** (next section).

### BYOB: clarifying questions & decision matrix

Use this when the user wants BYOB-accurate sandbox behavior (per MT docs). Ask
only what you need; one or two focused questions often suffice.

1. **Bank simulation goal**
  - **Integration / PO lifecycle / webhooks / “money moves fast”** → prefer **GWB** (`example1`, nickname `GWB`) for originating accounts and POs that should complete end-to-end.
  - **Reconciliation rules / EP matching / you control when things match** → prefer **IBB** (`example2`, nickname `IBB`) for internal accounts used in expected-payment stories; use **GWB** for PO-heavy legs if the demo mixes both.
2. **Expected payments vs payment orders (typical split)**
  - Docs guidance: **PO testing** often uses **GWB** internal accounts; **EP + reconciliation** testing often uses **IBB** internal accounts (with transactions you create or import). If the demo is EP-only on IBB, do not assume POs will “hit the bank” on those same IAs.
3. **Simulation tricks (only if the demo needs them)**
  - **Check success:** counterparty account number `123456789`.
  - **Check failure:** address line `1111 Azkaban Unit 321`.
  - **Simulated ACH / wire return (receive a return, not originate one):** receiving account number `100XX` where `XX` is the ACH return code (e.g. `10001` → R01). **EFT returns:** `101XX` pattern per docs. **Wire “returns”** use a similar counterparty pattern; simulated wire returns may not carry a return **code**.
  - **Unoriginated / inbound credit:** simulate via **incoming payment detail** flows; can combine with **virtual accounts** (sandbox VA numbers are generated for you).
  - **IPD into a VA:** sandbox supports `simulations/incoming_payment_details/create_async` style flows per docs.
4. **Timing**
  - Sandbox timings **differ from production** (e.g. ACH-like flows settle in seconds; checks may have ~5 minute delay). Set user expectations in prose if needed; do not assume production latency in the JSON.
5. **Connections cannot be deleted** — still true for BYOB; choose GWB vs IBB deliberately per internal account.

### Capability table (reference)


| `entity_id`       | Typical dataloader use                                                        |
| ----------------- | ----------------------------------------------------------------------------- |
| `modern_treasury` | **Default.** Standard demos, PSP templates, most generated configs.           |
| `example1`        | **BYOB only** — GWB-style fast PO + auto transactions for EP on GWB IAs.      |
| `example2`        | **BYOB only** — IBB-style EP reconciliation without PO processing on IBB IAs. |


Every config that creates internal accounts needs at least one connection.
Connections cannot be deleted.

### Live orgs: reuse connections, avoid duplicates

When the run uses **org discovery + reconciliation**, the engine **maps** your
config `connections[].entity_id` to each live connection’s `vendor_id`. If
nothing matches (org created outside this config), the run **reuses** an
existing org connection when possible instead of creating a duplicate sandbox
connection.

**Authoring:** Use **one** `connections[]` entry by default
(`modern_treasury`). Do **not** add connections “to be safe.” Extra connections
are for **BYOB** or explicit multi-bank demos, not for splitting USD vs USDC on
the same PSP.

---

## Legal Entities

A legal entity is a person or business. Required for KYC/KYB onboarding.

`**ref` keys:** Prefer company- or role-shaped names (`acme_payments`,
`psp_operator`, `platform_entity`). Avoid bare `platform` — it collides with
the word “platform” in prose and is easy to mis-resolve in large configs
(`naming_conventions.md`).

### Legal entity `connection_id`

- `**modern_treasury` / default PSP:** Omit `connection_id` on `legal_entities[]`
in JSON. Create uses inferred or runtime-selected PSP connection.
- **BYOB (`example1` / `example2`):** Include `connection_id` only when the
scenario requires it on legal-entity create; otherwise omit.

### Legal entity — omit compliance blobs

Omit `identifications`, `addresses`, and `documents` from JSON. The run fills
sandbox-safe mock data for those shapes.


| `legal_entity_type` | Fields you provide                                                                           | Auto-managed / omit in DSL (do NOT include)                                                                                                                              |
| ------------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `business`          | `ref`, `legal_entity_type`, `business_name`, optional `legal_structure`, optional `metadata` | Omit: `identifications`, `addresses`, `documents`, `date_formed`, `country_of_incorporation`. **PSP:** omit `connection_id` on `modern_treasury` unless BYOB requires it |
| `individual`        | `ref`, `legal_entity_type`, `first_name`, `last_name`, optional `email`, optional `metadata` | Omit: `identifications`, `addresses`, `documents`, `date_of_birth`, `citizenship_country`. **PSP:** omit `connection_id` on `modern_treasury` unless BYOB requires it    |


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


| Intent                           | Config section   | Key fields                                                              |
| -------------------------------- | ---------------- | ----------------------------------------------------------------------- |
| External party with bank account | `counterparties` | `name`, `accounts[]` (inline bank accounts), optional `legal_entity_id` |


### Inline accounts on counterparties

Each counterparty can have one or more `accounts[]`. These are created
inline with the counterparty and auto-registered as child refs:

- `$ref:counterparty.<key>.account[0]` — first account
- `$ref:counterparty.<key>.account[1]` — second account (if present)

**Allowed fields** on each `accounts[]` object include: `account_type`,
`party_name`, `party_type`, `party_address`, `account_details`, `routing_details`,
`metadata`, plus sandbox-only `sandbox_behavior` and `sandbox_return_code`, and
`wallet_account_number_type` for **stablecoin wallet** counterparties (see below).

### Stablecoin wallet accounts (Modern Treasury)

#### Create a Wallet Counterparty (MT API shape)

Modern Treasury creates wallet counterparties with `**accounts[].account_details`**
only — **no** `routing_details`. Each detail row uses the on-chain address as
`account_number` and a network-specific `account_number_type`:

```json
{
  "name": "Vendor Wallet",
  "accounts": [
    {
      "account_details": [
        {
          "account_number": "<COUNTERPARTY_WALLET_ADDRESS>",
          "account_number_type": "ethereum_address"
        }
      ]
    }
  ]
}
```

In dataloader JSON, that lives under `**counterparties[]**` with a `ref`, same
`name` / `accounts` structure, and optional `legal_entity_id` / `metadata` as
elsewhere in this file.

**Supported `account_number_type` values** for stablecoin wallets (network-specific;
must match the chain you intend):


| Network      | `account_number_type`  |
| ------------ | ---------------------- |
| Ethereum     | `ethereum_address`     |
| Base         | `base_address`         |
| Polygon PoS  | `polygon_address`      |
| Arbitrum One | `arbitrum_one_address` |
| Solana       | `solana_address`       |
| Stellar      | `stellar_address`      |


- Use `**wallet_account_number_type`** on the inline account to auto-fill a **sandbox
demo** address and the correct `account_number_type` (still **no** `routing_details`).
- Or omit that helper and set `**account_details`** explicitly (production-style address).
- **Do not** set `sandbox_behavior` on wallet accounts: it applies ACH test
account numbers and routing, which is the wrong shape for wallets.

**Not allowed:** `name` on the inline account (schema uses `extra="forbid"`).
The counterparty itself has `name`; for an account-level label use `party_name`
or a string in `metadata` (e.g. `account_label`).

### Sandbox behavior (critical for demos)

Set `sandbox_behavior` on the account to control how the sandbox processes
payments sent to this counterparty:


| `sandbox_behavior` | Effect                                           | Magic account number                    |
| ------------------ | ------------------------------------------------ | --------------------------------------- |
| `success`          | Payment completes normally                       | `123456789`                             |
| `return`           | Payment auto-returns with the specified ACH code | `100XX` (where XX = return code digits) |
| `failure`          | Payment fails outright                           | `1111111110`                            |


When `sandbox_behavior` is set, `account_details` and `routing_details`
are auto-populated — you do not need to specify them. Set
`sandbox_return_code` alongside `return` (e.g. `"R01"` for NSF).

**When the config includes `counterparties`, set `sandbox_behavior` on every
inline bank account used for outbound ACH/wire/RTP PO demos.** Wallet
counterparties use `wallet_account_number_type` or explicit `account_details`
instead. Without bank sandbox flags where needed, the sandbox may use
unpredictable outcomes. Configs with **no** counterparties (e.g. internal-only
`book` transfers) do not need this.

---

## Internal Accounts

An internal account is a bank account owned by the platform. In PSP/marketplace
models, each user gets their own internal account as a "wallet."


| Intent                            | Config section      | Key fields                                                           |
| --------------------------------- | ------------------- | -------------------------------------------------------------------- |
| Platform operating account        | `internal_accounts` | `connection_id`, `name`, `party_name`, `currency`, `legal_entity_id` |
| Per-user wallet (PSP/marketplace) | `internal_accounts` | Same — `legal_entity_id` links to the user's LE                      |
| Platform revenue/fee account      | `internal_accounts` | Same — `legal_entity_id` links to the **platform's** LE              |


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
already created, when you need an account with a ledger account attached, or
when `**funds_flows`** bank `**slots**` should use a **named top-level ref**
(`$ref:external_account.…`) that stays stable across reuse or org reconciliation
(see note below).


| Intent                                           | Config section      | Key fields                                              |
| ------------------------------------------------ | ------------------- | ------------------------------------------------------- |
| Additional bank account on existing counterparty | `external_accounts` | `counterparty_id`, `account_details`, `routing_details` |
| Bank account with inline ledger account          | `external_accounts` | Same, plus `ledger_account` (inline)                    |


Use **inline `accounts[]`** when you need `**sandbox_behavior**` or
`**wallet_account_number_type**` (those live only on inline rows). **For
`funds_flows.instance_resources` bank accounts** that steps reference via
`**@actor:user_N.bank`**, prefer **standalone `external_accounts[]`** plus
`**$ref:external_account.<key>**` when you care about **stable explicit refs**
after config round-trips or reconciliation — `**$ref:counterparty.<key>.account[0]`**
can warn or break if nested accounts are not restored identically. Still use
standalone rows for a **second** account on the same counterparty or an **inline
ledger account** on the EA.

`**sandbox_behavior` is not valid on `external_accounts[]`.** It exists only on
**counterparty inline `accounts[]`** (`CounterpartyAccountConfig`). Standalone
external accounts must use normal `account_details` / `routing_details`. Do not add
`sandbox_behavior` to `external_accounts[]` rows.

**For generated configs:** include explicit `**account_details`** and `**routing_details`** on every `**external_accounts[]**` row unless the example or schema you are copying clearly documents omission (e.g. a specialized on-chain shape). Default to populated bank fields — empty or missing detail blocks often produce unusable or warning-heavy configs.

**Inline vs standalone are not interchangeable:** fields allowed on
counterparty inline accounts (e.g. `sandbox_behavior`, `wallet_account_number_type`)
are **not** automatically valid on `**external_accounts[]`**. Use the schema for
each shape; provide bank fields explicitly on standalone external accounts.

---

## Virtual Accounts (Rare)

Use **sparingly.** Most PSP and marketplace demos should use **internal
accounts as wallets** only.

A virtual account is a sub-account on an IA for **per-payer inbound
attribution** on the **same** real bank account — not a separate wallet
balance.


| Intent                          | Config section     | Key fields                                                |
| ------------------------------- | ------------------ | --------------------------------------------------------- |
| Per-payer inbound routing label | `virtual_accounts` | `name`, `internal_account_id`, optional `counterparty_id` |


**Do not** use virtual accounts for PSP wallet balances — use internal
accounts. Only add a VA when the demo story is specifically about VA-based
inbound attribution (uncommon).

---

## Payment Orders

A payment order moves money. This is the most common resource in demos.


| Intent                             | `type`                  | `direction` | Accounts                                                                                   |
| ---------------------------------- | ----------------------- | ----------- | ------------------------------------------------------------------------------------------ |
| Pay a vendor / supplier            | `ach`, `wire`, or `rtp` | `credit`    | `originating_account_id` = internal account, `receiving_account_id` = counterparty account |
| Collect from a customer (drawdown) | `ach`                   | `debit`     | `originating_account_id` = internal account, `receiving_account_id` = counterparty account |
| Move funds between wallets (book)  | `book`                  | `credit`    | Both are `internal_account` refs                                                           |
| Collect platform fee               | `book`                  | `credit`    | From user wallet IA to platform revenue IA                                                 |
| Payout to external bank            | `ach`, `wire`           | `credit`    | From user IA to counterparty external account                                              |


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


| Intent                                    | Config section      | Key fields                                                |
| ----------------------------------------- | ------------------- | --------------------------------------------------------- |
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


| Intent                           | Config section             | Key fields                                                                                                                                                                                                                                               |
| -------------------------------- | -------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Simulate inbound credit to an IA | `incoming_payment_details` | `type`, `direction`, `amount`, `internal_account_id` (optional `originating_account_number` / `originating_routing_number` for some rails). **No** `originating_account_id` on raw rows — use **funds_flows** IPD steps if you need that ref in the DSL. |


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


| Intent                          | Config section                                 | Key fields                                                          |
| ------------------------------- | ---------------------------------------------- | ------------------------------------------------------------------- |
| Create a ledger                 | `ledgers`                                      | `name`, `description`                                               |
| Create a ledger account         | `ledger_accounts`                              | `name`, `ledger_id`, `normal_balance` (credit or debit), `currency` |
| Standalone ledger transaction   | `ledger_transactions`                          | `ledger_entries[]` (at least one debit + one credit, must balance)  |
| Inline ledger transaction on PO | `ledger_transaction` field on `payment_orders` | Same `ledger_entries` structure                                     |


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


| Intent                  | Config section              | Key fields                                        |
| ----------------------- | --------------------------- | ------------------------------------------------- |
| Create a category       | `ledger_account_categories` | `name`, `ledger_id`, `normal_balance`, `currency` |
| Add account to category | `category_memberships`      | `category_id`, `ledger_account_id`                |
| Nest categories         | `nested_categories`         | `parent_category_id`, `sub_category_id`           |


---

## Returns

Return an **incoming payment detail** (inbound item). `**sandbox_behavior`
does not apply to IPDs** — only to POs sent to counterparty accounts.


| Intent                         | Config section | Key fields                                       |
| ------------------------------ | -------------- | ------------------------------------------------ |
| Return / bounce an inbound IPD | `returns`      | `returnable_id` → IPD, optional `code`, `reason` |


**Returns do not support metadata.**

For **outbound** PO failures / NSF demos to a buyer's bank, use
`sandbox_behavior: "return"` on the counterparty **and** an ACH PO — that is
not the same as an IPD return.

---

## Reversals

Reverse a completed/sent payment order. The PO must reach `approved`,
`sent`, or `completed` status before a reversal can be created.


| Intent                  | Config section | Key fields                   |
| ----------------------- | -------------- | ---------------------------- |
| Reverse a payment order | `reversals`    | `payment_order_id`, `reason` |


`reason` values: `duplicate`, `incorrect_amount`,
`incorrect_receiving_account`, `date_earlier_than_intended`,
`date_later_than_intended`.

The handler automatically polls the PO status until it reaches a reversible
state (up to 60s). Not all sandbox connections support reversals.

---

## Staged Resources (Cross-Cutting)

**Default:** Omit `staged` on PO/IPD/EP/LT. Live-fire is controlled from the **run UI**
unless the config intentionally embeds `staged: true` (e.g. `examples/staged_demo.json`).

Four resource types support `staged: true`: **payment_order**,
**incoming_payment_detail**, **expected_payment**, and
**ledger_transaction**. Staged resources are skipped during the normal run
and appear as "Fire" buttons in the run-detail UI.

**When `staged: true` belongs in JSON (uncommon for generated configs):**

- A hand-maintained demo file where every step is pre-marked staged.
- Testing validator / DAG rules for staged chains.

**When to omit `staged` and use the UI instead:**

- Typical SE walkthrough: presenter stages or fires from **run UI** after load.
- Non-interactive batch stories.

**When NOT to use staged (in JSON):**

- The resource is a prerequisite for other non-staged resources (validator rule).

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

## Funds Flows (lifecycle patterns)

When the demo involves a multi-step payment lifecycle, use the `funds_flows`
DSL section. This replaces manually building individual `payment_orders`,
`incoming_payment_details`, `ledger_transactions`, `returns`, and
`reversals` sections.

**Use funds_flows when:**

- The demo involves 2+ related payment/ledger steps
- The SE wants to visualize the money flow
- The SE will run many copies of the same flow pattern with distinct parties
- The demo involves lifecycle variants (returns, reversals, alternative payout methods)
- The demo has **variable `user` parties** — each needs `**instance_resources` on that `funds_flows[]` row** (see `**system_prompt.md` → *User actors (mandatory JSON)***); not optional for distinct payors/payees
- The demo needs ledger transaction lifecycle (pending → posted → archived
via `transition_ledger_transaction` steps)

`**instance_resources` — required for every `user` actor that should not be the same party on every copy of the flow:**

- Define **that flow’s** per-copy `legal_entities`, `counterparties`, `internal_accounts`, `external_accounts`, and ledger rows (as needed) **under `funds_flows[].instance_resources`**, with `**{instance}` in each `ref**` and in every `**$ref:legal_entity.…` / `$ref:counterparty.…` / `$ref:external_account.…**` that `**user_N.entity_ref**` and **slots** point at.
- **Placeholders:** `{instance}` (and `{ref}` where used) isolate copies. For **name fields**, use `**{user_1_business_name}`**, `**{user_2_business_name}**`, `**{user_1_first_name}**`, etc. — one **actor-scoped** key per `**user_N`** row — for every `legal_entities` / `counterparty` / `party_name` template that belongs to a different `user` actor. **Do not** repeat bare `**{business_name}`** on two party rows in the same flow when they map to **different** `user_1` / `user_2` (it collapses both parties to one synthetic company). Global `{business_name}` is only for **one** business party per instance or non-actor labels. Details: `**metadata_patterns.md`** § *Multi-`user_N` (scaling)*.
- You may still use global `{first_name}`, `{last_name}`, `{industry}`, `{country}` where a single profile row is intentional (e.g. one individual party).
- `**direct_N`** actors use **top-level** static resources and literal `**customer_name`** — not `instance_resources` for the platform identity.

**Do not** wire `**user_N.entity_ref`** to a **single** top-level `legal_entity` ref that has **no `{instance}*`* except when the user **explicitly** asks for **one reused participant across every copy** of the flow.

**Raw top-level lifecycle arrays (`payment_orders`, `incoming_payment_details`, …):** for **generated** configs, **prefer `funds_flows`** even in advanced cases. Use raw arrays **only** when editing **compiled output**, fixing a **known non-DSL edge case**, or maintaining legacy hand-authored rows — not as the default authoring model.