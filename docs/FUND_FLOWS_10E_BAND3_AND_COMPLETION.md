# Plan 10e — Band 3 spec + completion backlog

This document closes the gap between the **first 10e implementation slice** (drawer shell, Band 1/5, read-only Band 4, placeholder Band 2/3) and the **subplan end state** described in the parent Fund Flows UI plan. It lives under `docs/` so implementers are not blocked on private `plan/` copies.

---

## Why the first slice looked “half done”

The subplan text lists **five bands** and success criteria that read like a single release. In practice the work was split so the drawer could ship without blocking on **11a** (actor library), **UX research** (dense money-movement matrix), or a large **scenario-builder extraction**. The slice table in `plan/.../10e_config_drawer_bands.md` documents that split; this file turns the remainder into **actionable specs and PR-sized work**.


| Area            | First slice                                  | Remaining                                                                                    |
| --------------- | -------------------------------------------- | -------------------------------------------------------------------------------------------- |
| Band 5 + Band 1 | Done in drawer                               | Hardening, contrast, `config_version` on writes (10h)                                        |
| Band 2          | Placeholder table                            | Binding dropdown + persist after **11a** `LoaderDraft`                                       |
| Band 4          | Read-only `staging_rules` + `timing` summary | Interactive staging + **instance spread** (see below)                                        |
| Band 3          | Placeholder                                  | **This document** § Band 3 — implement after Band 4 or in parallel once row layout is agreed |


---

## Band boundary: money movement vs staging & timing

**Ground truth today:** `templates/partials/scenario_builder.html` groups **variance + per-step T0/T+N** inside “Money Movement”, and puts **staging rules** and **instance spread (days)** in separate sections. `**POST /api/flows/recipe-patch`** accepts a merged patch; `static/js/scenario-builder.js` `buildRecipe()` shows the exact JSON shape.

**Recommended drawer split (10e):**


| Band                     | Owns (recipe / UI)                                                                                                                                                                                                                                                    | Does not own                                                                                                                        |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **3 — Money movement**   | `amount_variance_min_pct`, `amount_variance_max_pct`, `step_variance` (per `step_id`: omit key = global, `{}` = locked, `{min_pct,max_pct}` = custom), **per-step** `timing.step_offsets`, **anchor** `timing.start_date` when edited from the first amount row (T0). | `staging_rules`, `staged_`* legacy, `timing.instance_spread_days` / `spread_pattern` / `spread_jitter_days` (those are **Band 4**). |
| **4 — Staging & timing** | `staging_rules`, instance spread fields on `RecipeTimingConfig`, and any **flow-level** delay defaults that the product wants surfaced here vs JSON-only (`step_delay_overrides` / pattern `flow_timing` — see open point below).                                     | Per-step amount variance toggles (Band 3).                                                                                          |


**Rationale:** Matches the existing accordion sections, minimizes duplicate controls, and aligns with `10_fund_flows_ui.md` (“Staging, timing & money-movement calendar”) where **10f** can later add a **derived** calendar that reads both bands.

**Open point (resolve in Band 4 PR):** `RecipeTimingConfig` also has `step_delay_overrides` (hours). The scenario builder UI today does not expose it. Either keep it JSON-only, or add a collapsed “Advanced timing” row in Band 4 with documented semantics from `flow_compiler/generation_pipeline.py`.

---

## Band 3 — Executable specification

### Goal

Operators can configure **scaled amount variance** and **business-day offsets from the anchor (T0)** for each money-movement step **inside the config drawer**, with the same behavior as the scenario builder, without opening the list-page accordion.

### Preconditions

- `flow_summary_dict_at_index` / `flow_summary.amount_steps` already provides `step_id`, `type`, `amount`, and variance UI fields via `_step_variance_ui_fields` (`dataloader/routers/flows/helpers.py`).
- Recipe read/write: `sess.generation_recipes[recipe_flow_ref]` and `POST /api/flows/recipe-patch` with `{ flow_ref, patch }` (`dataloader/routers/flows/api.py`).

### UI requirements (parity with scenario builder)

1. **Section header:** “Band 3 — Money movement” (existing); replace placeholder copy when implemented.
2. **Matrix header row:** Step | Type | Variance control | Schedule (T0 / T+N) | Amount (read-only, formatted as today).
3. **Variance (same state machine as `scenario-builder.js`):**
  - Three modes per row: **global** (open lock), **locked** (closed lock, `step_variance[step_id] = {}`), **custom** (pencil, min/max % inputs).
  - Global min/max `%` row below the matrix (maps to `amount_variance_min_pct` / `amount_variance_max_pct`).
4. **Schedule:**
  - First amount step: **T0** pill + date button + hidden `<input type="date">` → `timing.start_date` (ISO date string in patch).
  - Later steps: **T+N** pill + numeric offset → `timing.step_offsets[step_id]`; default offset equals row index when absent (same as JS: only send overrides when `val !== defaultVal`).
5. **Empty state:** If `amount_steps` is empty, show short copy: “No amount steps in this pattern” (no Apply).
6. **Accessibility:** Lock / custom control meets 44×44px tap target (`10e` drawer contract); pills and inputs have `title` / `aria-label` matching current scenario builder intent.

### Save semantics

- **Apply / Save** for Band 3 (alone or combined with Band 4 footer — product choice):
  - Build a **patch object** containing only keys this band owns (partial recipe), e.g. `{ "amount_variance_min_pct": …, "amount_variance_max_pct": …, "step_variance": {…}, "timing": { "start_date": …, "step_offsets": … } }`.
  - **Merge:** `dataloader/flows_mutation.py` `merge_recipe_dict` already **deep-merges** `timing` (per-key) and **merges** `timing.step_offsets`; a patch that only sets `start_date` / `step_offsets` does **not** remove `instance_spread_days`. Still add regression tests when Band 3 ships.
- On success: HTMX re-swap drawer **or** toast + optional `hx-trigger` list refresh — same pattern as Band 5 metadata save.

### Server / validation

- Reuse `GenerationRecipeV1.model_validate` path already used by `recipe-patch`.
- Add **focused tests:** round-trip variance modes + step_offsets + start_date for a fixture flow with multiple amount steps.

### Dependencies / ordering

- **Can ship after** interactive Band 4 **or in parallel**; no hard dependency on 11a.
- **Soft dependency:** If Band 4 is still read-only, users may still edit spread in the accordion; document “spread only in scenario builder” until Band 4 ships.
- **Future (10f):** Optional “preview next settlement dates” strip under Bands 3–4; not required for Band 3 v1.

### Acceptance criteria (Band 3 done)

- Drawer Band 3 shows all `amount_steps` rows with correct initial state from `generation_recipes[flow_ref]`.
- Changing variance modes and global min/max persists via `recipe-patch` and survives full page reload.
- T0 date and T+N offsets persist and match generation behavior (`flow_compiler` timing).
- No regression to Band 5 / metadata or Band 1 display fields.
- Contrast / tap-target checks for new controls (same gate as drawer).

---

## Band 4 — Interactive completion (checklist)

**Goal:** Staging rules + instance spread editable in the drawer; remove or shorten duplicate sections in the scenario builder once parity is verified (deprecation copy in accordion optional).

1. **Context:** Ensure `optional_groups` labels are available for staging `<select>` options (already on `flow_summary`).
2. **UI:** Port staging block from `scenario_builder.html` / `addStagingRule` / `resetStagingRules` behaviors into a partial (vanilla JS module or small inline, consistent with Band 5).
3. **Timing (Band 4 slice):** `timing-spread` → `timing.instance_spread_days`; document whether `spread_pattern` / `spread_jitter_days` stay JSON-only for v1.
4. **Save:** Same `recipe-patch` merge rules; tests for staging_rules array round-trip.
5. **Read-only path:** Keep summary view as fallback when JS disabled, or server-render initial rows.

---

## Band 2 — Reminder

Binding column + `library_actor_id` persistence remains **11a**-gated per `docs/FUND_FLOWS_10E_RISK_AND_HYDRATION.md`. Do not block Band 3/4 on it.

---

## File / API index


| Artifact                                        | Role                                                                                 |
| ----------------------------------------------- | ------------------------------------------------------------------------------------ |
| `templates/partials/scenario_builder.html`      | Current UX reference for Bands 3+4 fields                                            |
| `static/js/scenario-builder.js`                 | `buildRecipe`, variance, timing, staging serialization                               |
| `models/flow_dsl.py`                            | `GenerationRecipeV1`, `RecipeTimingConfig`, `StagingRule`                            |
| `dataloader/routers/flows/api.py`               | `recipe-patch`, `recipe-to-working-config`                                           |
| `dataloader/routers/flows/helpers.py`           | `_step_variance_ui_fields`                                                           |
| `dataloader/view_models/flows_config_drawer.py` | Extend context if JSON clients need typed `amount_steps` (already on `flow_summary`) |


---

## Revision history

- **2026-04-11:** Initial Band 3 spec + completion backlog (addresses “plan only 50% done” by making deferred bands PR-sized and testable).

