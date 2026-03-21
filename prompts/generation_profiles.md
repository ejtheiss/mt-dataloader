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
2. **List sections** you will include (use the scope ladder).
3. **Pick a structural template:** mirror `examples/psp_minimal.json` or
   `examples/marketplace_demo.json`—do not invent a third architecture unless
   the user clearly needs something neither file covers.
4. **Add extras** only if the user explicitly asked (second step of the ladder).

If the user’s request is vague, **ask one short question**: *“Smallest possible
demo (one internal transfer), or full marketplace-style onboarding + flows?”*

---

## Scope levels (pick one primary)

### A — Minimal slice

**Use when the user wants:** the smallest thing that runs; “hello world”; one
movement of money inside the platform; no parties, no KYB story.

**Structural template:** `examples/psp_minimal.json`  
**Typical sections:** `connections`, `internal_accounts`, `payment_orders` (often
one `book` PO).

**Usually omit:** legal entities, counterparties, IPDs, fees, ACH—unless the user
asked for any of those.

---

### B — Demo-rich (default when unclear)

**Use when the user wants:** a **customer-facing** PSP / marketplace story:
onboarded parties, wallets, settlement, fees, maybe sandbox ACH / returns.

**Structural template:** `examples/marketplace_demo.json`  
**Typical sections:** connections, legal entities, counterparties (with
`sandbox_behavior` where relevant), internal accounts as wallets (+ platform
revenue IA), payment orders (`book` + `ach` as the story needs), optional IPD
only for **simulated inbound** when the script calls for it.

**Do not add by default:** `expected_payments`, `virtual_accounts`, ledger
sections—see decision rubrics; only if the user explicitly wants recon / VA /
accounting demos.

---

### C — Extended (explicit user request only)

**Use when the user clearly asks for:** reconciliation matching, ledgering,
virtual-account attribution, or IPD return objects—not because “more is better.”

**Do not use the word “lifecycle” with the user**—say **extended** or **full-platform
extras** so it isn’t confused with payment order lifecycle.

**May add (on request):** `expected_payments`, `ledgers` / `ledger_accounts` /
`ledger_transactions`, `virtual_accounts`, explicit `return` on IPDs, etc., in
line with `decision_rubrics.md`.

---

## Quick mapping (user language → scope)

| User intent (examples) | Scope | Template |
|------------------------|-------|----------|
| “Smallest”, “one transfer”, “minimal PSP” | A | `psp_minimal.json` |
| “Marketplace”, “buyer/seller”, “wallets”, “settle”, “fee”, “Boats-style” | B | `marketplace_demo.json` |
| “Reconciliation”, “expected payment”, “match inbound” | C | B + EP/IPD per rubrics |
| “Ledger”, “double-entry”, “GL” | C | B + ledger sections per rubrics |
| “Per-payer routing”, “VA”, “sub-accounts for attribution” | C | B + VA per rubrics |

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
| `expected_payments` | No | Yes |
| `virtual_accounts` | No | Yes |
| Ledgers / categories / ledger TXNs | No | Yes |

---

## After you pick scope

1. Generate **complete** `DataLoaderConfig` JSON (no placeholders).
2. Validate mentally against **self-bootstrap** (connection + resources in-file).
3. Run **`POST /api/validate-json`** (or user does); fix errors by path.

If scope and rubrics conflict, **rubrics win** on object choice; **this doc** only
limits how much you build.
