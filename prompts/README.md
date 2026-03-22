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
| `chatgpt_app_instructions.md` | `decision_rubrics.md`, `naming_conventions.md`, `ordering_rules.md`, `metadata_patterns.md`, `generation_profiles.md`, JSON schema from `GET /api/schema`, `examples/*.json` |

The instructions contain behavioral guidance, all 17 generation rules, output
format, connection capabilities, and the validation loop — everything the LLM
needs in context every turn. The knowledge files are reference material
retrieved as needed.

### B. Monolithic system prompt (raw API)

Use `system_prompt.md` as a template: paste each `<PASTE_*_HERE>` placeholder
with the corresponding file contents + schema + examples. This produces one
large (~50KB+) system message suitable for direct API calls where you control
the full context window.

---

## File inventory

| File | Role |
|------|------|
| **`chatgpt_app_instructions.md`** | **ChatGPT app Instructions field.** Merged behavioral guidance + generation rules + output format + validation loop. Self-contained — does not need placeholders filled. |
| **`system_prompt.md`** | **Monolithic template.** Workflow, output format, placeholder slots for all docs below, generation rules, validation loop. |
| **`generation_profiles.md`** | **Scope only** — minimal vs demo-rich vs extended vs staged; which sections to include; mirrors `examples/*.json`. |
| **`decision_rubrics.md`** | **Which MT resource** to use for a given intent (PSP defaults, IPD vs PO, when to add EP/VA/ledger). Per-type staged subsections and cross-cutting staged-resources section. |
| **`ordering_rules.md`** | DAG behavior, when to add `depends_on`, IPD/PO wording, staged resource DAG constraints. |
| **`naming_conventions.md`** | `ref` keys and `$ref:` patterns. |
| **`metadata_patterns.md`** | Suggested metadata keys by vertical; string values only. |

**Ground truth for shape:** `GET /api/schema` + `POST /api/validate-json` + the
files under `examples/` (including `staged_demo.json`).

**Contradictions:** Schema and validator win; then `decision_rubrics`; this
README is descriptive only.
