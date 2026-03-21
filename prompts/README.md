# LLM prompt kit (`prompts/`)

These files are **not** loaded by the dataloader app. Compose them into your
external LLM system prompt (see `system_prompt.md` for the shell and paste
order).

| File | Role |
|------|------|
| **`system_prompt.md`** | Master template: workflow, **output format**, placeholders for schema + docs below, generation rules, validation loop. |
| **`generation_profiles.md`** | **Scope only** — minimal vs demo-rich vs extended; which sections to include; mirror `examples/*.json`. |
| **`decision_rubrics.md`** | **Which MT resource** to use for a given intent (PSP defaults, IPD vs PO, when to add EP/VA/ledger). |
| **`ordering_rules.md`** | DAG behavior, when to add `depends_on`, IPD/PO wording. |
| **`naming_conventions.md`** | `ref` keys and `$ref:` patterns. |
| **`metadata_patterns.md`** | Suggested metadata keys by vertical; string values only. |

**Ground truth for shape:** `GET /api/schema` + `POST /api/validate-json` + the two files under `examples/`.

**Contradictions:** Schema and validator win; then `decision_rubrics`; this README is descriptive only.
