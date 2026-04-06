# Config scope (for the LLM)

**What this document is for:** Decide **how large** the config should be and **which
sections to include**—*before* you write JSON. It does **not** replace
`decision_rubrics.md` (which MT object to use) or `ordering_rules.md` (DAG /
`depends_on`). Use those after you know the scope.

**What this document is not:** JSON Schema, field-level rules, or naming—those
come from `/api/schema`, `naming_conventions.md`, and `system_prompt.md` rules.

---

## Procedure (do this in order)

1. **Classify the ask** using the triggers below (one primary scope).
2. **List static/bootstrap sections** you need (`connections`, `internal_accounts`,
   `legal_entities`, `counterparties`, ledgers, etc.) plus **`funds_flows`**.
   Default **one** `modern_treasury` connection even when IAs use both USD and
   USDC (`stablecoin_ramp.json`).
3. **Pick a structural template** from **Funds Flow examples only** — mirror
   shape from `examples/psp_minimal.json` (smallest flow), `examples/marketplace_demo.json`
   (PSP marketplace), or `examples/funds_flow_demo.json` (ledger lifecycle).
   Do not invent a third architecture unless neither file fits.
4. **Add extras** only if the user explicitly asked (second step of the ladder).

If the user's request is vague, **ask one short question**: *"Smallest **funds
flow** (e.g. one internal book transfer in `funds_flows`), or full marketplace
onboarding + flows?"* Never answer "minimal" by emitting **raw** top-level
`payment_orders[]` without `funds_flows`.

---

## Scope levels (pick one primary)

### A — Minimal slice

**Use when the user wants:** the smallest thing that runs; "hello world"; one
movement of money inside the platform; no parties, no KYB story.

**Structural template:** `examples/psp_minimal.json` — already **Funds Flows
DSL**: `connections`, `internal_accounts`, and **one `funds_flows` entry** whose
`steps` contain a single `payment_order` step (`book` transfer via `@actor:`
slots). **Do not** put that PO only in top-level `payment_orders[]` with no
`funds_flows`.

**Usually omit:** legal entities, counterparties, IPDs, fees, ACH—unless the user
asked for any of those.

---

### B — Demo-rich (default when unclear)

**Use when the user wants:** a **customer-facing** PSP / marketplace story:
onboarded parties, wallets, settlement, fees, maybe sandbox ACH / returns.

**Structural template:** `examples/marketplace_demo.json`  
**Typical sections:** connections (`ref:` e.g. `platform_bank`, **`entity_id:
"modern_treasury"`**, PSP-style nickname such as `"Modern Treasury PSP"`),
`instance_resources` (LEs, CPs, IAs), and **`funds_flows`** with steps for
deposit / settle / fee / payout (and optional IPD steps for simulated inbound).
Express **all** POs and IPDs as **steps**, not as hand-written top-level arrays.
**Do not** use `example1` / `example2` here unless the user asked for **BYOB**
(see `decision_rubrics.md`). **PSP legal entities:** never emit `connection_id`
on `legal_entities[]` — BYOB-only when required (`decision_rubrics.md`).

**Do not add by default:** `expected_payments`, `virtual_accounts`, ledger
sections—see decision rubrics; only if the user explicitly wants recon / VA /
accounting demos.

---

### C — Extended (explicit user request only)

**Use when the user clearly asks for:** reconciliation matching, ledgering,
virtual-account attribution, or IPD return objects—not because "more is better."

**Do not use the word "lifecycle" with the user**—say **extended** or **full-platform
extras** so it isn't confused with payment order lifecycle.

**May add (on request):** `expected_payments`, `ledgers` / `ledger_accounts` /
`ledger_transactions`, `virtual_accounts`, explicit `return` on IPDs, etc., in
line with `decision_rubrics.md`.

---

## Quick mapping (user language → scope)

| User intent (examples) | Scope | Template |
|------------------------|-------|----------|
| "Smallest", "one transfer", "minimal PSP" | A | `psp_minimal.json` |
| "Marketplace", "buyer/seller", "wallets", "settle", "fee" | B | `marketplace_demo.json` |
| "Lifecycle", "deposit-to-settle", "flow pattern" | B | `funds_flow_demo.json` |
| "Live demo", "fire one-by-one", "click-through" | B | `marketplace_demo.json` or `funds_flow_demo.json` — **do not** ask about `staged`; SE uses **run UI** for live-fire. `staged_demo.json` only if the user wants JSON with `staged: true` baked in. |
| "Stablecoin", "on-ramp", "off-ramp", "USDC", "USDG" | C | `stablecoin_ramp.json` (one `modern_treasury` connection; USD + USDC IAs on same `connection_id`) |
| "Brokerage", "rewards", "chart of accounts" | C | `tradeify.json` |
| "Reconciliation", "expected payment", "match inbound" | C | B + EP/IPD per rubrics |
| "Ledger", "double-entry", "GL" | C | B + ledger sections per rubrics |
| "Per-payer routing", "VA", "sub-accounts for attribution" | C | B + VA per rubrics |

If two rows apply, use the **highest** scope they need (e.g. marketplace + recon → C).

---

## Scope ladder (PSP / marketplace default)

Start at **B** unless the user chose **A** or **C**.

| Layer | Include in B (default demo) | Add only in C (explicit ask) |
|-------|-----------------------------|--------------------------------|
| Connections + IAs as wallets | Yes | — |
| LEs + CPs + sandbox_behavior | Yes | — |
| Book settle / fee / ACH payout | Yes | — |
| IPD (sandbox inbound simulation) | If the story needs it | — |
| `staged` on PO/IPD/EP/LT | **No** (default) — SE enables live-fire in **UI** | Only if user explicitly asks for `staged: true` in JSON |
| `expected_payments` | No | Yes |
| `virtual_accounts` | No | Yes |
| Ledgers / categories / ledger TXNs | No | Yes |

---

## Funds Flows DSL is the only authoring model

**Always author money movement with `funds_flows`.** There is **no** separate
"raw lifecycle DSL" for you to use: do **not** hand-write top-level
`payment_orders`, `incoming_payment_details`, `expected_payments`,
`ledger_transactions`, `returns`, or `reversals`. Omit those keys or leave them
absent; the compiler fills them from **`funds_flows[].steps`** and
**`optional_groups`**.

| Always via `funds_flows` | Never hand-author at top level |
|--------------------------|--------------------------------|
| Every PO, IPD, EP, LT, return, reversal step | Parallel copies in `payment_orders[]` / `incoming_payment_details[]` / … |
| Scaling (`instance_resources`, recipes) | "Minimal" configs with only raw arrays |
| `optional_groups` (NSF, alt payout, returns) | |

The compiler generates concrete resources from step definitions — your job is
**steps + actors + static/bootstrap resources**, not duplicate lifecycle rows.

---

## After you pick scope

1. **Choose structure:** non-empty **`funds_flows`** (at least one flow with at
   least one step) for any config that moves money; static sections only for
   shared infra (connections, accounts, counterparties, ledgers, etc.).
2. Generate **complete** `DataLoaderConfig` JSON (no placeholders).
3. Validate mentally against **self-bootstrap** (connection + resources in-file).
4. Run **`POST /api/validate-json`** (or user does); fix errors by path.

If scope and rubrics conflict, **rubrics win** on object choice; **this doc** only
limits how much you build.
