# Plan 10e — Band 3 spec + completion backlog

This document closes the gap between the **first 10e implementation slice** (drawer shell, Band 1/5, read-only Band 4, placeholder Band 2/3) and the **subplan end state** described in the parent Fund Flows UI plan. It lives under `docs/` so implementers are not blocked on private `plan/` copies.

**Status (2026-04-11):** Bands **3** and **4** are **interactive in the config drawer** (shared **Apply recipe (bands 3–4)** → `recipe-patch`). Band **2** has **Save bindings** + library dropdowns in the drawer (`POST /api/flows/{i}/actor-bindings`). The **Actors registry** band on `/flows` (11a Phase 1) and contrast / `config_version` hardening remain. **11a Phase 0** data path: `[FUND_FLOWS_11A_PHASE0.md](FUND_FLOWS_11A_PHASE0.md)`.

---

## Why the first slice looked “half done”

The subplan text lists **five bands** and success criteria that read like a single release. In practice the work was split so the drawer could ship without blocking on **11a UI** (Actors registry + binding dropdowns), **UX research** (dense money-movement matrix), or a large **scenario-builder extraction**. The slice table in `plan/.../10e_config_drawer_bands.md` documents that split; this file turns the remainder into **actionable specs and PR-sized work**.


| Area            | First slice                                  | Done since first slice                                                                                                                                                                                         | Still to build                                                                                                                    |
| --------------- | -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| Band 5 + Band 1 | Done in drawer                               | —                                                                                                                                                                                                              | Hardening, contrast, `config_version` on writes (10h)                                                                             |
| Band 2          | Placeholder table                            | **11a Phase 0** + **drawer:** binding dropdown + `POST …/actor-bindings` + `recompose` (`[FUND_FLOWS_11A_PHASE0.md](FUND_FLOWS_11A_PHASE0.md)`)                                                                  | **11a Phase 1:** always-visible **Actors** registry CRUD on `/flows` (full identity forms)                                            |
| Band 4          | Read-only summary                            | **Drawer:** staging rows + instance spread + shared Apply with Band 3 (`flow_config_drawer_band234.html`, `recipe-patch`)                                                                                    | Optional: `spread_pattern` / `spread_jitter`, `step_delay_overrides`; trim duplicate scenario-accordion copy when confident          |
| Band 3          | Placeholder                                  | **Drawer:** variance matrix + T0 / T+N + shared Apply (`static/js/flow-config-drawer.js`)                                                                                                                      | **10f** calendar digest; edge cases around clearing `step_variance` keys (same as scenario builder merge semantics)                    |


---

## 11a coordination (Phase 0 vs Band 2 / Band 3)

- **Phase 0 (landed):** Persistence and projection — library rows, per-recipe bindings, legacy ids, materialize into `actor_overrides`, refresh `legacy:…` rows from recipe so scenario builder and compose stay consistent (`[FUND_FLOWS_11A_PHASE0.md](FUND_FLOWS_11A_PHASE0.md)`).
- **Still 11a:** Operator-visible **Actors** registry on `/flows` (CRUD library rows). **Band 2** in the drawer already **mutates `actor_bindings`** and recomposes; registry remains the place for full identity editing per parent plan.
- **Band 3** has **no** dependency on 11a; it only touches `recipe-patch` / variance / `timing` keys already owned by the scenario builder today.

---

## Band boundary: money movement vs staging & timing

**Ground truth today:** `templates/partials/scenario_builder.html` groups **variance + per-step T0/T+N** inside “Money Movement”, and puts **staging rules** and **instance spread (days)** in separate sections. `POST /api/flows/recipe-patch` accepts a merged patch; `static/js/scenario-builder.js` `buildRecipe()` shows the exact JSON shape.

**Recommended drawer split (10e):**


| Band                     | Owns (recipe / UI)                                                                                                                                                                                                                                                    | Does not own                                                                                                                               |
| ------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **3 — Money movement**   | `amount_variance_min_pct`, `amount_variance_max_pct`, `step_variance` (per `step_id`: omit key = global, `{}` = locked, `{min_pct,max_pct}` = custom), **per-step** `timing.step_offsets`, **anchor** `timing.start_date` when edited from the first amount row (T0). | `staging_rules`, `staged_*` legacy fields, `timing.instance_spread_days` / `spread_pattern` / `spread_jitter_days` (those are **Band 4**). |
| **4 — Staging & timing** | `staging_rules`, instance spread fields on `RecipeTimingConfig`, and any **flow-level** delay defaults that the product wants surfaced here vs JSON-only (`step_delay_overrides` / pattern `flow_timing` — see open point below).                                     | Per-step amount variance toggles (Band 3).                                                                                                 |


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

- **Can ship after** interactive Band 4 **or in parallel**; **no** hard or soft dependency on **11a** (Phase 0 does not change this).
- If Band 4 is still read-only, users may still edit spread in the accordion; document “spread only in scenario builder” until Band 4 ships.
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

## Band 2 — What Phase 0 changed vs what remains

**Done (11a Phase 0):** Typed `**LibraryActorEntry`**, `**LoaderDraft.actor_library` / `actor_bindings`**, `**SessionState**` mirrors, draft merge + `**legacy:{recipe}:{frame}**` hydration, `**sync_legacy_library_rows_from_recipes**` + `**materialize_actor_bindings_to_generation_recipes**` before compose, and `**FlowConfigDrawerContext**` exposes `**actor_library**` + `**actor_bindings**` for JSON/HTML consumers (`[FUND_FLOWS_11A_PHASE0.md](FUND_FLOWS_11A_PHASE0.md)`).

**Remaining (10e + 11a Phases 1–2):** Always-visible **Actors registry** on `GET /flows`; **Band 2** binding matrix with **dropdown** bound to `actor_bindings`, explicit save/recompose, and copy that points operators to the registry for full identity editing (per parent plan). Do **not** block Band 3/4 on this.

---

## File / API index


| Artifact                                             | Role                                                                                                         |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------ |
| `templates/partials/scenario_builder.html`           | Current UX reference for Bands 3+4 fields                                                                    |
| `static/js/scenario-builder.js`                      | `buildRecipe`, variance, timing, staging serialization                                                       |
| `models/flow_dsl.py`                                 | `GenerationRecipeV1`, `RecipeTimingConfig`, `StagingRule`                                                    |
| `dataloader/routers/flows/api.py`                    | `recipe-patch`, `recipe-to-working-config`                                                                   |
| `dataloader/routers/flows/helpers.py`                | `_step_variance_ui_fields`                                                                                   |
| `dataloader/view_models/flows_config_drawer.py`      | Drawer context; `**actor_library` / `actor_bindings`** on session (11a P0); `amount_steps` on `flow_summary` |
| `dataloader/actor_library_runtime.py`                | Plan **11a** hydrate / sync / materialize (see `[FUND_FLOWS_11A_PHASE0.md](FUND_FLOWS_11A_PHASE0.md)`)       |
| `models/loader_draft.py` / `models/actor_library.py` | **11a** draft + `LibraryActorEntry` shapes                                                                   |


---

## Revision history

- **2026-04-11:** Initial Band 3 spec + completion backlog (addresses “plan only 50% done” by making deferred bands PR-sized and testable).
- **2026-04-11:** Marked document **complete** for Band 3 spec purposes; folded in **11a Phase 0** (data path only); split Band 2 “done vs remaining”; fixed `staged_*` table cell; expanded file index and coordination section.
- **2026-04-11:** Bands **2–4** interactive drawer shipped; status + summary table updated to match implementation.