# Fund Flows Plan 10e — risk register + research hydration

This document **manages 10e risks** called out in `plan/.../10e_config_drawer_bands.md` (local plan) and parent `10_fund_flows_ui.md`. **Tracked** so implementers do not rely on private `plan/` copies.

---

## Preconditions (repo state)

| Gate | Status |
|------|--------|
| **10d** (drawer a11y, `inert`, teardown, wide-panel hook point) | **On `main`** (merged via PR #30). |
| **10c** (case card, `display_title` / `display_summary` on `FundsFlowConfig`, metadata API) | **On `main`**. |
| **Plan 17 Phase B** (`resolve_mt_display_label` / NW-6+) | **Not in codebase** under that name. **Do not block 10e** on it: Band 1 uses **working JSON + compile DTOs** until 17 lands. |

---

## Risk: `display_title` / `display_summary` drift (shim vs cutover)

**Issue:** `/flows` list read titles from **compiled expanded rows** (`getattr(fc, "display_title")`) while `POST /api/flows/{i}/metadata` writes **`funds_flows[edit_idx]`** in `working_config_json`. After a save, the list could show **stale** titles until re-compile.

**Resolution (full cutover):** `get_funds_flow_display_fields_for_display_row` in `dataloader/routers/flows/helpers.py` — **prefer** `working_config_json` / `config_json_text` → `funds_flows[resolve_working_funds_flow_index_for_metadata(session, display_idx)]` → `display_title` / `display_summary`; **fallback** to expanded row only when JSON keys absent or working JSON unusable.

**Call sites:** `dataloader/routers/flows/page.py` (`flows_page` loop). **Future:** `build_flow_config_drawer_context` (10e) must use the **same helper** for Band 1 — do not re-derive from IR only.

**Tests:** `tests/test_flow_metadata_display.py` (`test_display_fields_*`).

---

## Risk: `authoring_config_json` null / pre-compile sessions

**Research:** `SessionState` (`dataloader/session/__init__.py`) may have `authoring_config_json=None` until validation; **`generation_recipes`** and **`working_config_json`** are populated on the happy path.

**Index resolution:** `resolve_working_funds_flow_index_for_metadata` requires **non-empty** working (or `config_json_text`) JSON with `funds_flows`. If empty, it **raises** — `/flows` normally redirects unvalidated users to `/setup`.

**10e implication:** `config-drawer` route must return **4xx or redirect** when session cannot resolve `flow_idx`; never guess an index silently.

---

## Risk: Band 2 vs Plan **11a** (actor library)

**Plan:** Binding table + dropdowns depend on **11a** `LoaderDraft` extensions.

**Mitigation:** Ship Band 2 as **read-only or placeholder** until 11a; no `LoaderDraft` mutation from 10e without coordinated migration (`loader_draft_from_session` / tests per parent plan).

**Canonical plan pointers:** `plan/.../10e_config_drawer_bands.md` — band table row **2**, § **Actor config surface**, Research preflight **§2**; `plan/.../11a_shared_actor_library_flow_bindings.md` — binding matrix, registry, materialization.

---

## Band 4 — read-only slice vs full “migrate” (10e)

**Local plan** row **4** says *Migrate from scenario builder* (interactive staging / spread / anchor in the drawer). The **first implementation slice** on `feat/plan-10e-config-drawer` only shows a **read-only** summary from the stored recipe; **editing** still happens in the **scenario builder accordion** on `/flows`.

**Deferred?** **Interactive Band 4 is remaining 10e work** (further PRs on the same subplan), not handed off to 11a. **11a** only strips **actor** controls from Band 4 concepts (see 11a § staging UI); **timing + staging rules** remain **10e** once migrated.

**Update the plan doc:** `plan/.../10e_config_drawer_bands.md` § **Implementation slices (multi-PR)** — added so this split is explicit in the private plan tree.

---

## Risk: Band 3 (money movement)

**Status:** **Specified for implementation** — see **[`FUND_FLOWS_10E_BAND3_AND_COMPLETION.md`](FUND_FLOWS_10E_BAND3_AND_COMPLETION.md)** (Band 3 executable spec, merge semantics, acceptance criteria). The first 10e slice kept a **placeholder** only; that was a sequencing choice, not “out of scope.”

**Residual risk (low):** `merge_recipe_dict` already merges `timing` per-key (`dataloader/flows_mutation.py`). Band 3 should still ship with **regression tests** so future refactors do not regress merge behavior.

---

## Risk: Metadata editor migration (`flows_view.html` → drawer Band 5)

**Current behavior (detail page):**

| Concern | Location |
|---------|----------|
| Trace key + primary template + KV rows | `templates/flows_view.html` + `saveMetadata()` |
| Step metadata | `collectKV` on `.metadata-step-entries` |
| **Does not yet POST** `display_title` / `display_summary` | Extend payload when Band 5 adds those controls (API already accepts them in `dataloader/routers/flows/api.py`). |

**Client validation:** None beyond HTTP errors; server enforces `forbidden_trace_keys` and field lengths.

---

## Risk: Contrast gate (Phase 2b)

**Tooling:** `scripts/check_contrast.py` exists for automated checks; manual pass still required for drawer partial before ship.

---

## Risk: Template partial strategy (10e § jinja2-fragments)

**Decision for v1:** **Option 1** — plain `{% include %}` for `flow_config_drawer.html` / band partials; full drawer re-swap on save. Upgrade later if per-band HTMX is required.

---

## Where it landed (branch `feat/plan-10e-config-drawer`)

| Deliverable | Files |
|-------------|--------|
| `GET /api/flows/{i}/config-drawer` | `dataloader/routers/flows/partials.py` → `partials/flow_config_drawer.html` |
| `GET /api/flows/{i}/config` (JSON) | `dataloader/routers/flows/api.py` |
| Typed context | `dataloader/view_models/flows_config_drawer.py` |
| List row summary reuse | `dataloader/routers/flows/flow_list_row.py` + `page.py` |
| Wide drawer | `static/css/drawer.css`, `static/js/mt-drawer.js`, `static/css/flow-config.css`, `base.html` |
| Deep link `?flow=&panel=config` | `templates/flows.html` (opens drawer via HTMX) |
| Case card “Flow config” | `templates/partials/case_card.html` |
| Detail read-only strip | `templates/flows_view.html` |
| Band 1 titles | `get_funds_flow_display_fields_for_display_row` via `flow_summary_dict_at_index` |

### Follow-up (same plans, later PRs)

| Item | Owner doc | Notes |
|------|-----------|--------|
| Band 2 binding dropdown + persist | **10e** + **11a** | After `LoaderDraft` / library exists per 11a. |
| Band 4 interactive staging/timing + recipe save | **10e** | Move controls from `scenario_builder.html` pattern; not 11a except “no actor rows in Band 4”. |
| Band 3 money-movement matrix | **10e** | Spec: **`docs/FUND_FLOWS_10E_BAND3_AND_COMPLETION.md`** — variance + T0/T+N in drawer; optional 10f calendar later. |

---

## `config_version` (prep for 10h)

Not yet in session context. When adding `FlowConfigDrawerContext`, either hash `working_config_json` slice for that flow or defer until 10h — document in first 10e PR if omitted.
