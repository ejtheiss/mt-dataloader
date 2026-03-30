# LLM prompt kit (`prompts/`)

These files are **not** loaded by the dataloader app. They configure an external
LLM (ChatGPT custom app, API call, etc.) to generate valid `DataLoaderConfig`
JSON.

---

## Two usage modes

### A. ChatGPT custom app (recommended)

Use `chatgpt_app_instructions.md` as the **Instructions** field. Upload the
remaining files as **Knowledge files** that the app retrieves on demand:

| Instructions field | Knowledge files (upload all) |
|---|---|
| `chatgpt_app_instructions.md` | `decision_rubrics.md`, `naming_conventions.md`, `ordering_rules.md`, `metadata_patterns.md`, `generation_profiles.md`, `validation_fixes.md`, JSON schema from `GET /api/schema`, all `examples/*.json` |

The instructions contain behavioral guidance, generation rules, output format,
Funds Flows DSL step types, and the validation loop. The knowledge files are
reference material retrieved as needed.

### B. Monolithic system prompt (raw API)

Use `system_prompt.md` as a template: paste each `<PASTE_*_HERE>` placeholder
with the corresponding file contents + schema + examples. This produces one
large (~50KB+) system message suitable for direct API calls where you control
the full context window.

---

## File inventory

| File | Role |
|------|------|
| **`chatgpt_app_instructions.md`** | **ChatGPT app Instructions field.** Behavioral guidance + generation rules + output format + Funds Flows DSL + validation loop. Self-contained. |
| **`system_prompt.md`** | **Monolithic template.** Workflow, output format, placeholder slots for all docs below, generation rules, Funds Flows DSL with step types and optional_groups, validation loop. |
| **`generation_profiles.md`** | **Scope selection** — minimal / demo-rich / extended; which sections to include; when to use `funds_flows` vs raw arrays. |
| **`decision_rubrics.md`** | **Which MT resource** to use for a given intent (PSP defaults, IPD vs PO, NSF patterns, **`modern_treasury` default connections**, **BYOB-only** `example1`/`example2` + GWB/IBB matrix, ledger_entries shape, staged resources, cleanup reference). |
| **`ordering_rules.md`** | DAG behavior, `depends_on`, funds_flows step ordering, staged resource constraints. |
| **`naming_conventions.md`** | `ref` keys, `$ref:` patterns, per-type naming table (including `transition_ledger_transaction`). |
| **`metadata_patterns.md`** | Suggested metadata keys by vertical; string values only. |
| **`validation_fixes.md`** | Common validation error patterns and fixes (including funds_flows errors). |

**Ground truth for shape:** `GET /api/schema` + `POST /api/validate-json` + the
files under `examples/`.

**Contradictions:** Schema and validator win; then `decision_rubrics`; this
README is descriptive only.

---

## Example files

| File | Use for |
|------|---------|
| `funds_flow_demo.json` | Funds Flows DSL starter: actors, optional_groups, transition_ledger_transaction |
| `marketplace_demo.json` | PSP marketplace: instance_resources, NSF return edge case |
| `stablecoin_ramp.json` | Fiat↔stablecoin: dual connections, exclusion_group payout alternatives |
| `tradeify.json` | Ledger-heavy brokerage: categories, per-user scaling, USDG |
| `staged_demo.json` | Staged demo with "Fire" buttons |
| `psp_minimal.json` | Minimal book-transfer-only config |
