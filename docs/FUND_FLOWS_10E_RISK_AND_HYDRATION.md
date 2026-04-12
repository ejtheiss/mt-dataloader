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

---

## Risk: Band 3 (money movement)

**Status:** **Explicitly on hold** in 10e — heading + placeholder only. No implementation risk for v1.

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

---

## `config_version` (prep for 10h)

Not yet in session context. When adding `FlowConfigDrawerContext`, either hash `working_config_json` slice for that flow or defer until 10h — document in first 10e PR if omitted.
