# Architecture: name and display pipelines

This document is the **in-repo** source for how **actor keys, human labels, preview rows, hydrated resource names, and UI strings** relate across validate, generation, session, and display code.

**It is not a cycle or backlog ledger.** Sequencing, PR grouping, and initiative ownership stay in maintainer-local **`plan/`** (gitignored). If text here conflicts with a locked product decision, **§0.5 wins** until code matches.

## Implementation status (repository scan)

**Most of §0.5 and NW-1…NW-9 are still target behavior, not finished work.** Spot-check with `rg` on the paths below when this section drifts.

| Item | Status | Where to look |
|------|--------|----------------|
| **NW-1** — gate Preview until Apply | **Not done** | `templates/flows.html` always links to `/preview`; `dataloader/routers/setup/pages.py` `preview_page` only requires a session. |
| **NW-2** — block Execute if preview stale | **Not done** | `dataloader/routers/execute.py` uses `config_hash` for DB run rows, not to compare against last `build_preview`. |
| **NW-3** — `resolve_mt_display_label` | **Not done** | Symbol absent; `dataloader/preview_labels.py` still falls back to `flow_compiler.actor_display_name`. |
| **NW-4** — grouped preview actor strip = MT names | **Not done** | `build_flow_grouped_preview` still sets `"alias"` from `flatten_actor_refs` keys (wiring), not from `preview_items` / `mt_display_name`. |
| **NW-5** — Mermaid uses shared labels | **Not done** | `flow_compiler/mermaid.py`: `_build_ref_display_map`, `_resolve_actor_display`, `actor_display_name` still drive participants. **Mandatory execution checklist:** see **§ NW-5 — mandatory checklist** below (do not rely on informal “we’ll fix Mermaid later”). |

### NW-5 — mandatory checklist (Mermaid shared labels)

**Do not ship NW-5 as a drive-by.** Every PR that claims **NW-5** progress must tick the relevant boxes and update **`docs/ARCHITECTURE_NAMING_AND_DISPLAY.md`** § *Implementation status* when status flips.

1. **Gate — NW-3 done first**  
   - **`resolve_mt_display_label`** (or the **actual** exported name chosen in **10** Phase B) exists, is tested, and is used by at least one **preview** path.  
   - If the shipped API **differs** from older plan prose (`preview_by_typed`, parameter names, etc.), **update** maintainer-local **`plan/3.31.26 plans-data loader/08_compiler_mermaid_scope.md`** § *Plan 17 Phase C* and **`17a`** § *Code health — Mermaid* in the **same PR** as the code — plans follow code, not the reverse.

2. **Wire Mermaid to the resolver**  
   - Build **`ref_labels: dict[str, str]`** (or pass through whatever structure NW-3 defines) at **`pass_render_diagrams`** and **`generate_from_recipe`** call sites, using **session / preview / plan** context when available.  
   - **`render_mermaid`** must accept that input; **pre-Apply / headless** paths use the **documented** slug fallback (no alias+slot display map on the user-visible path).

3. **Delete legacy display-map usage from the Mermaid path only after (2) works**  
   - Remove **`_build_ref_display_map`**, **`_resolve_actor_display`**, and **`actor_display_name`** from the **diagram** code path per **`17a`** Part B §B.8 / **§19** register — **not** before tests pass with resolver labels.  
   - **`flow_compiler/display.py`** / **`__init__.py` exports:** coordinate with **NW-6 / NW-9** sweeps; do not leave orphan callers.

4. **Docs in lockstep**  
   - Update this file’s **NW-5** row to **Done** only when grep confirms the legacy path is gone from **`mermaid.py`** participant resolution.  
   - Add/update a **01_cycle_ledger.md** PR row for each merge (table stakes per cycle rules).

5. **Regression tests**  
   - **Syrupy** (or equivalent) golden strings for **at least one** representative diagram after NW-5; keep **`tests/test_mermaid_and_dry.py`** coverage.  
   - Resolver unit tests live next to NW-3; Mermaid tests assert **labels match** preview policy, not duplicate map logic.
| **NW-6** — fund-flow columns | **Not done** | `flow_compiler/flow_views.py` still imports `build_ref_display_map` / `resolve_actor_display`. |
| **NW-7** — case card participants | **Treat as open** | Needs a focused template/router pass to confirm. |
| **NW-8** — one expansion world | **Not done** | `flow_compiler/pipeline.py` `pass_expand_instances` still uses hardcoded `default_profile` demo strings. |
| **NW-9** — remove parallel display helpers | **Not done** | `flow_compiler/display.py`, `flow_compiler/__init__.py` exports, and many tests still use the ref-display map API. |

**Already aligned (partial):** `build_preview`, `extract_display_name`, and `templates/partials/preview_resource_row.html` implement the **per-row** preview labeling story (§5a). `resolve_resource_display` walks config and uses `extract_display_name` before slug fallback — but Mermaid, fund-flow views, and grouped **actors** still use the **parallel** alias+slot pipeline.

### `plan/3.31.26 plans-data loader/` (gitignored)

Tracked code only **mentions** that path for **conventions** (e.g. `.github/workflows/ci.yml`, `pyproject.toml` comments). **NW backlog and “Plan 17” ordering are not in the public repo**; they live in maintainer-local plans under that folder (e.g. unified names / fund-flows initiatives). This file describes **architecture + code reality**; **which cycle ships which NW item** stays in `plan/`.

---

## 0) Vocabulary (do not conflate)

| Concept | Meaning | Example |
|--------|---------|---------|
| **Frame key** | Dict key under `funds_flows[].actors` | `user_1`, `direct_1` |
| **Human alias** | `ActorFrame.alias` — story label | `Buyer`, `Platform` |
| **Slot binding** | `flatten_actor_refs` entry | key `user_1.bank` → `$ref:external_account.…` |
| **Pre-actor** | `@actor:frame.slot` before compile | Resolved in `resolve_actors` |
| **Profile keys** | String map for `deep_format_map` / templates | `{user_1_business_name}`, `{instance}` |
| **Hydrated name** | Field on an emitted resource config | `legal_entity.business_name`, `counterparty.name`, `party_name` |
| **Preview label** | Row in `preview_items` | `display_name`, `mt_display_name` |
| **Ref slug label** | Heuristic from `$ref:` string | `actor_display_name` in `mermaid.py` — **legacy fallback only**; user-facing surfaces target the shared MT-name path (§0.5) |

---

## 0.5) Locked product and engineering decisions

These override earlier “optional” or “two world” wording until implementation catches up.

| Decision | Rule |
|----------|------|
| **Preview before Apply** | **Not allowed.** Operators do not open **Execution plan / Preview** until **Apply** (compose) has run. UI: **grey out / disable** the Preview entry. |
| **Single source of MT-shaped truth** | `session.config` **after compose** is the **only** contract for payloads that match what **Execute** sends to MT. **`preview_items`** = projection of that config for humans (`extract_display_name` + reconciliation → `display_name` / `mt_display_name`). |
| **One expansion world** | **Consolidate** validate-time **demo** expansion (`pass_expand_instances` `default_profile`) with recipe-driven expansion long-term; short term, **gating Preview** reduces user-facing confusion. **No** permanent “two truths” for names the operator trusts. |
| **One display pipeline (target)** | **Ideally one** resolver for “label for this `$ref:` / `typed_ref`” across app. **Maximum two** only if strongly justified and documented. **Mermaid, fund-flow ledger/payments views, grouped preview, case-card participants** must use the **same** labels as MT-facing resource names (same pipeline as preview rows), **not** `actor_display_name` / alias+slot maps. |
| **Preview / Execution plan** | For resources on that page: **`preview_items` + `typed_ref` only** — no parallel `actor_display_name` or authoring-only labels. |
| **Grouped preview actor strip** | Each actor binding shows the **MT resource name** as it will appear in MT (via preview row resolution / `mt_display_name` rules), **not** `frame.slot` masquerading as `alias`. |
| **Case card participants** | Always **names as they will appear in MT** (post-compose hydrated / same resolver as preview), not DSL variables or slot paths. |
| **Execute vs preview freshness** | If config changes after the last **`build_preview`**, **block Execute** until preview is rebuilt (re-apply or explicit “refresh preview” that re-runs dry_run + `build_preview`). |

---

## 1) Authoring input (JSON / DSL)

| Location | What “name” means | Wired to |
|----------|-------------------|----------|
| `funds_flows[].actors` keys | Frame keys | `@actor:{key}.{slot}`, `actor_overrides[{key}]`, profile `{key}_*` |
| `ActorFrame.alias` | Human label (DSL / intent) | Scenario builder chip, authoring UX — **not** an MT display name; MT labels come from shared resolver post-compose (§0.5) |
| `ActorFrame.slots` | `$ref:` targets | `flatten_actor_refs`, validation, views |
| `ActorFrame.entity_ref` | LE anchor for user frames | Instance LE rows after expansion |
| `ActorFrame.customer_name` | Literal for direct frames | Profile literals; UI in scenario builder / drawer |
| `ActorFrame.dataset`, `name_template`, `entity_type` | Faker / template choice | `_build_instance_profile` |
| `instance_resources` templates | Placeholders in JSON | `deep_format_map` + `_bind_bare_business_name` |
| `trace_value_template` | Trace string | Second expansion path in compile (see compiler docs) |
| Step payloads | `@actor:` / `$ref:` | `resolve_actors` in `compile_flows` |
| `DataLoaderConfig.customer_name` | Top-level branding (legacy) | **Target:** same shared label pipeline as other surfaces (§0.5) |

**Consolidation:** Treat **frame key**, **alias**, and **slot path** as three explicit fields everywhere we serialize actors for UI (today one field is overloaded as `alias` in grouped preview — see §5f).

---

## 2) Validate-time compiler (`compile_to_plan`)

| Pass | File | Name-related behavior |
|------|------|------------------------|
| **expand instances** | `flow_compiler/pipeline.py` `pass_expand_instances` | Uses **hardcoded** `default_profile` — **not** Faker, **not** recipes. Expands `instance_resources` with `instance="0000"`. |
| **compile → IR** | `flow_compiler/core.py` `compile_flows` | `flatten_actor_refs` + `resolve_actors`; `expand_trace_value` with instance `0` unless template already substituted. **Plan 08** split modules: **`docs/FLOW_COMPILER_CORE_MODULES.md`**. |
| **emit** | `flow_compiler/core_emit.py` `emit_dataloader_config` (re-exported from `core` / package) | Resources land in `config` with expanded template names |
| **Mermaid** | `pass_render_diagrams` | `render_mermaid(ir, fc, customer_name=authoring.config.customer_name)` |
| **Fund flow views** | `pass_compute_view_data` | `compute_view_data` → `build_ref_display_map` / `resolve_actor_display` (alias + slot — **target:** shared resolver, §0.5) |

---

## 3) Generation / scenario apply (`generate_from_recipe`, compose)

| Stage | File | Name-related behavior |
|-------|------|------------------------|
| Orchestration | `flow_compiler/generation_pipeline.py` `run_generation_pipeline` | Plan 08 Track B: P0–P13 phase order; see **`docs/FLOW_COMPILER_CORE_MODULES.md`**. |
| Recipe | `models/flow_dsl.py` `GenerationRecipeV1` | `actor_overrides[frame_key]` → `customer_name`, `name_template`, `dataset`, `entity_type` |
| Profile | `flow_compiler/generation.py` `_build_instance_profile` | Per frame: literals or Faker via `seed_loader`; keys `{alias}_name`, `{alias}_business_name`, … |
| Clone / expand | `clone_flow`, `_expand_instance_resources` | `deep_format_map`, `_bind_bare_business_name` |
| Compile per instance | `compile_flows(flows, base_config)` | Flow `ref` like `pattern__0042` |
| Mermaid per instance | `generation.py` | `render_mermaid` — validate path passes `customer_name=`; generation path historically differed; **target:** one resolver for labels (§0.5) |

---

## 4) Session state (name-related fields)

| Field | Role |
|-------|------|
| `authoring_config_json` | Original patterns + `funds_flows` + `instance_resources` |
| `config` / `config_json_text` | Executable resources; **hydrated** names live here |
| `generation_recipes` | Overrides that change Faker/literals |
| `pattern_flow_ir` / `pattern_expanded_flows` | Snapshot from **validate** (demo profile expansion) |
| `flow_ir` / `expanded_flows` | After apply: **one IR + expanded flow per generated instance** |
| `_display_flow_session_sources` | If `generation_recipes` **and** `flow_ir`: show **generated**; else **pattern** |
| `preview_items` | Built from **current** `config` + DAG + reconciliation |
| `view_data_cache` | Recomputed on apply: `compute_view_data(ir, fc)` per pair |

After **Apply**, **`flow_ir` / `expanded_flows` / `view_data_cache` / `preview_items`** should all reflect the **same** composed config. **`pattern_*`** fields are **pre-apply compile artifacts** for structure, not MT-audit surfaces (§0.5).

---

## 5) Display pipelines (summary)

### 5a) Resource row labels (Setup / Preview tables)

- **Config:** `dataloader/engine/resource_display.py` `extract_display_name`
- **Preview row:** `dataloader/helpers.py` `build_preview` sets `display_name` / `mt_display_name` (reconciliation merges discovered names)
- **Template:** `templates/partials/preview_resource_row.html` prefers `mt_display_name` else `display_name`

### 5b) Resolve `$ref` without preview row

- **`preview_labels.resolve_resource_display`** — walk `typed_ref` variants on `all_resources(config)` → `display_label_from_resource` → else `actor_display_name`
- **Target:** one internal `resolve_mt_display_label(ref, preview_by_typed, config)` used across call sites (§0.5)

### 5c) Ref-slug pretty-print

- **`flow_compiler/mermaid.py` `actor_display_name`** — **last-resort fallback only** inside the shared resolver

### 5d–5e) Mermaid and fund-flow views

- **Current:** `_build_ref_display_map`, `resolve_actor_display` (alias + slot)
- **Target:** same resolver as preview; **`account_actor_map`** for **wiring** only, not human column titles

### 5f) Grouped preview “Actors” strip

- **`build_flow_grouped_preview`** — **target:** MT name per slot `$ref` via `preview_items` + typed-ref walk; rename misleading keys (e.g. `frame_slot`, `mt_display_label`)

### 5g–5m) Flows UI, org, execute, runs, discovery

- **`/flows` list / drawer:** intent labels OK for configuration; **participant summary / case card** → MT names post-compose
- **Org:** `_le_display_name`, reconciliation → `mt_display_name` overlay in `build_preview`
- **Execute SSE:** `extract_display_name` on resources — keep aligned
- **Runs / staged / cleanup:** `typed_ref`-first; optional later join to same `display_name` as preview

---

## 6) Client-side (scenario builder)

- **`static/js/scenario-builder.js`** posts `actor_overrides` keyed by **frame**; no separate name engine in JS

---

## 7) Consolidation matrix (problem → tactic)

| Problem | Tactic (§0.5) |
|---------|----------------|
| Preview reachable before Apply | **Gate UI**; optional server guard on `GET /preview` |
| Two expansion worlds (demo vs recipe) | **Gate** + **merge** `pass_expand_instances` toward recipe/shared profile |
| Parallel display pipelines | **One** `resolve_mt_display_label`; remove display use of `_build_ref_display_map` / alias+slot for user-visible strings |
| Grouped preview actor strip wrong | **MT name** from `preview_items` + typed-ref walk |
| Case card participants wrong | **MT names** via shared resolver; fix `actor_frames` if still wrong |
| Execute vs stale preview | **Config hash** vs last `build_preview`; block Execute + CTA to refresh |
| Tests | Update expectations when `display_label` / actor keys rename |

---

## 8) Suggested implementation order (technical)

1. Session flag + UI gate for Preview until Apply  
2. Preview freshness for Execute (config hash vs `build_preview`)  
3. Shared `resolve_mt_display_label` module  
4. Grouped preview actors + template + tests  
5. Mermaid labels via resolver  
6. `compute_view_data` / fund-flow columns via resolver  
7. Case cards / drawer participant summary  
8. Compiler: unify `pass_expand_instances` with recipe expansion (larger)

**Scheduling** of the above belongs in **`plan/`** backlogs, not in this file.

---

## 9) File index

| Area | Files |
|------|--------|
| DSL models | `models/flow_dsl.py`, `models/config.py` |
| Actor ref resolution | `flow_compiler/core.py` |
| Demo expansion | `flow_compiler/pipeline.py` |
| Faker / profile | `flow_compiler/generation.py`, `flow_compiler/seed_loader.py` |
| Mermaid display | `flow_compiler/mermaid.py`, `flow_compiler/display.py` |
| Fund flow views | `flow_compiler/flow_views.py` |
| Preview | `dataloader/helpers.py`, `dataloader/preview_labels.py`, `templates/partials/preview_resource_row.html` |
| Resource names | `dataloader/engine/resource_display.py` |
| Routes / session | `dataloader/routers/flows.py`, `dataloader/routers/setup/` (package: `pages`, `json_api`, `htmx_validate`, `drafts`, `resource_partials`), `dataloader/session/__init__.py` |
| Org names | `org/discovery.py`, `org/reconciliation.py` |
| Execute SSE | `dataloader/engine/runner.py` |
| Scenario UI | `templates/partials/scenario_builder.html`, `static/js/scenario-builder.js` |

---

## 10) Target spine (states)

**Authoring** → **Validate** (structural; demo expansion internal) → **Funds Flow UI** (recipes) → **Apply** → **`session.config` MT-shaped** → **`build_preview`** → **`preview_items`** → **Execute** (if preview fresh) → **MT**.

User-trusted **“name MT will see”** = **post-Apply** config + preview projection only (§0.5).

---

## 11) Target work items (NW-1 … NW-9)

Identifiers used across compiler, UI, and tests. **Full tables, deletion register, LoC estimates, and PR sequencing** may be maintained in maintainer-local **`plan/`** (e.g. unified names + fund-flows UI plans); this section lists **IDs only** for grep and code review.

| ID | Item |
|----|------|
| **NW-1** | Gate Preview until Apply |
| **NW-2** | Execute only if preview matches current config hash |
| **NW-3** | Shared `resolve_mt_display_label` |
| **NW-4** | Grouped preview actor strip → MT names |
| **NW-5** | Mermaid participant labels via resolver |
| **NW-6** | `compute_view_data` column titles via resolver |
| **NW-7** | Case card / overview participants → MT names |
| **NW-8** | Unify `pass_expand_instances` with recipe expansion |
| **NW-9** | Sweep: remove display use of `build_ref_display_map`, `resolve_actor_display`, `actor_display_name`; trim `display.py` / exports |

---

## 12) Deletion register (symbols — accountability)

When implementing NW-3–NW-9, use this checklist so parallel naming helpers are actually removed.

### `flow_compiler/mermaid.py`

| Symbol | Target action |
|--------|----------------|
| `_build_ref_display_map` | **Delete** for user-visible display; replace with precomputed labels from shared resolver |
| `_resolve_actor_display` | **Delete** or inline as dict lookup + single fallback |
| `actor_display_name` | **Delete** as public API; **one** private fallback next to `resolve_mt_display_label` |
| `_strip_currency_suffix`, `_normalise_cp` | **Delete** with slug pipeline if MT names replace it |
| `_resolve_ipd_source`, participant collectors | **Refactor** to consume `label_by_ref` built via resolver |
| `render_mermaid(..., customer_name=...)` | **Refactor** — labels from passed-in map, not alias mutation |

### `flow_compiler/display.py`

| Target | **Delete** file or reduce to non-display re-exports; **`ref_account_type`** → import from `ir` at call sites |

### `flow_compiler/flow_views.py`

| Target | Replace `build_ref_display_map` / `resolve_actor_display` imports with shared resolver or precomputed `ref → label` |

### `dataloader/preview_labels.py`

| Symbol | Target action |
|--------|----------------|
| `display_label_from_resource` | **Merge** into `resolve_mt_display_label` |
| `resolve_resource_display` | **Delete** or thin delegate |
| `actor_display_name` import | **Remove** when fallback is centralized |

### `dataloader/helpers.py`

| Target | Drop re-export of `resolve_resource_display` when superseded |

### Keep (foundation)

- **`extract_display_name`** (`resource_display.py`)
- **`build_preview`** (`helpers.py`)
- **`flatten_actor_refs`** (`flow_compiler/core.py`)

### Tests

Rewrite **`tests/test_mermaid_and_dry.py`**, **`tests/test_flow_views.py`**, **`tests/test_edge_cases.py`** grouped-preview tests when resolver lands — prefer **unit tests on `resolve_mt_display_label`** + **one** golden Mermaid case over large map snapshots.

### PR gate (grep)

- [ ] No imports of `build_ref_display_map`, `resolve_actor_display`, or `actor_display_name` outside the single fallback module (if any)
- [ ] `flow_compiler/display.py` removed or non-display-only
- [ ] `flow_compiler/__init__.py` exports trimmed

---

## 13) Historical note

A longer single document previously lived at **`docs/PLAN_NAMES_UNIFIED.md`** (removed so **`docs/`** stays application architecture, not cycle ledgers). This file carries the **technical** content; **merge history** still has the old file if you need the original prose verbatim.
