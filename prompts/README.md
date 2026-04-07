# Prompts (`prompts/`)

Reference material for tools that generate **`DataLoaderConfig`** JSON. Not loaded by the app at runtime.

---

## How to wire them

### A. ChatGPT custom app

**Instructions:** `chatgpt_app_instructions.md`. **Knowledge:** upload the rest for retrieval:

| Instructions field | Knowledge files (upload all) |
|---|---|
| `chatgpt_app_instructions.md` | `decision_rubrics.md`, `naming_conventions.md`, `ordering_rules.md`, `metadata_patterns.md`, `generation_profiles.md`, `validation_fixes.md`, JSON schema from `GET /api/schema`, all `examples/*.json` |

### B. Single system message (API)

Replace `<PASTE_*_HERE>` in `system_prompt.md` with the linked files, schema, and examples.

---

## File inventory

| File | Role |
|------|------|
| **`chatgpt_app_instructions.md`** | **ChatGPT app Instructions field.** Behavioral guidance + generation rules + output format + Funds Flows DSL + validation loop. Self-contained. |
| **`system_prompt.md`** | **Monolithic template.** Workflow, output format, placeholder slots for all docs below, generation rules, Funds Flows DSL with step types and optional_groups, validation loop. |
| **`generation_profiles.md`** | **Scope selection** — minimal / demo-rich / extended; static/bootstrap sections; **always** author moves via `funds_flows` (no raw lifecycle authoring). |
| **`decision_rubrics.md`** | **Which MT resource** to use for a given intent (PSP defaults, IPD vs PO, NSF patterns, **`modern_treasury` default connections**, **BYOB-only** `example1`/`example2` + GWB/IBB matrix, **PSP legal entities: never author `connection_id`** — BYOB when required, ledger_entries shape, staged resources reference + **UI-first live-fire**, cleanup reference). |
| **`ordering_rules.md`** | DAG behavior, `depends_on`, funds_flows step ordering, staged resource constraints. |
| **`naming_conventions.md`** | `ref` keys, `$ref:` patterns, per-type naming table (including `transition_ledger_transaction`). |
| **`metadata_patterns.md`** | Metadata keys; **`instance_resources` template variables**; **§ Multi-`user_N` (scaling)** — actor-scoped placeholders vs `{business_name}`. |
| **`validation_fixes.md`** | Common validation error patterns and fixes (including funds_flows errors). |

**Ground truth for shape:** `GET /api/schema` + `POST /api/validate-json` + the
files under `examples/`.

**Precedence:** `GET /api/schema` and validators → `decision_rubrics.md` → this README.

---

## Example files

| File | Use for |
|------|---------|
| `funds_flow_demo.json` | Funds Flows DSL starter: actors, optional_groups, transition_ledger_transaction |
| `marketplace_demo.json` | PSP marketplace: instance_resources, NSF return edge case |
| `stablecoin_ramp.json` | Fiat↔stablecoin: one `modern_treasury` connection, USD + USDC IAs, exclusion_group payout alternatives |
| `tradeify.json` | Ledger-heavy brokerage: categories, per-user `instance_resources`, USDG |
| `staged_demo.json` | Every money step has `staged: true`; default authoring omits `staged` and uses run **UI** |
| `psp_minimal.json` | Minimal book-transfer-only config |
