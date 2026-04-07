# Product model vs codebase — names, variables, preview, MT

**Status:** Aligned with `**[PLAN_NAMES_UNIFIED.md](PLAN_NAMES_UNIFIED.md)` §0.5 (locked decisions).** Earlier open questions are **closed** below.

---

## 1) Product model (locked)

- **Input JSON** holds **variables** and DSL **actors** (`@actor:`, templates).
- `**/flows` + JSON editor** = **intent**: patterns, **recipes**, **actors / bindings** (see `**11a`**, `**10`**).
- `**session.config` after Apply (compose)** = **one MT-shaped truth** — all variables for generated resources replaced by **literals / Faker** from the recipe pipeline.
- `**preview_items`** = **projection** of that truth: `extract_display_name` + reconciliation → `display_name` / `mt_display_name`.
- **Execution plan / Preview** = **audit** of what **Execute** will send to MT — **not** a tutorial on templating.

---

## 2) Closed decisions (was “open questions”)


| Topic                                      | Decision                                                                                                                                                                                   |
| ------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Preview before Apply?**                  | **No.** Preview entry **greyed / disabled** until Apply.                                                                                                                                   |
| **Two expansion worlds (demo vs recipe)?** | **Consolidate to one** (§0.5). **Short term:** gating removes user harm; **long term:** merge `pass_expand_instances` with recipe-driven expansion (NW-8).                                 |
| **Display pipelines**                      | **Target one** shared `**resolve_mt_display_label`**; **max two** only if documented. **Mermaid + fund-flow views + grouped actors + case cards** use **MT names**, not alias+slot / slug. |
| **Case card participants**                 | **Names as in MT** (same resolver as preview), post-compose.                                                                                                                               |
| **Execute vs stale preview**               | **Block Execute** until preview is rebuilt for current `config` (hash / revision check).                                                                                                   |


---

## 3) Terminology: Apply vs Execute


| Action              | Effect on names in `config`                                                                                                             |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| **Apply / compose** | **Faker + literals** via `_build_instance_profile` + `deep_format_map` — this is when template variables become **MT payload strings**. |
| **Execute**         | Sends **current** `config` through `resolve_refs` to MT — **no** second naming pass.                                                    |


Preview must reflect **post-Apply** config only (§0.5).

---

## 4) Alignment with plans **10** / **11a** / **11**


| Plan                                                                                                                               | Fit                                                                                                                                 |
| ---------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `**[10_fund_flows_ui.md](../plan/3.31.26%20plans-data%20loader/10_fund_flows_ui.md)`**                                             | Dashboard **before** Execution plan; chrome must implement **Preview gate** + copy.                                                 |
| `**[11a_shared_actor_library_flow_bindings.md](../plan/3.31.26%20plans-data%20loader/11a_shared_actor_library_flow_bindings.md)`** | Library + bindings = **intent**; **materialized** names still land in `config` after compose; participant summaries → **MT names**. |
| `**[11_per_actor_drawer.md](../plan/3.31.26%20plans-data%20loader/11_per_actor_drawer.md)`**                                       | **Superseded** by **11a** — consistent with **one** identity surface + **one** composed config.                                     |


---

## 5) Engineering verdict

**Sound:** Single truth (`config` after Apply) + single projection (`preview_items`) + shared resolver for **all** MT-facing labels.

**Refactor required:** Remove **parallel** naming (`_build_ref_display_map`, `actor_display_name` for user-visible strings); add **Preview gate**, **Execute freshness guard**, then **unify compiler expansion** (backlog **NW-1–NW-8** in `[PLAN_NAMES_UNIFIED.md](PLAN_NAMES_UNIFIED.md)` §17).

**Tooling:** No external product; implement `**resolve_mt_display_label`** once and thread `preview_items` into Mermaid generation.

---

## 6) Related doc

Full inventory, transition tables, backlog IDs, **LoC / two-state analysis**, and **symbol-level deletion register**: **[`PLAN_NAMES_UNIFIED.md`](PLAN_NAMES_UNIFIED.md)** (§0.5, §5, §8, §10, §17, §18, **§19**).

**Actionable cycle plan (implementation phases + Mermaid):** **[`plan/3.31.26 plans-data loader/17_unified_names_preview_pipeline.md`](../plan/3.31.26%20plans-data%20loader/17_unified_names_preview_pipeline.md)** — backlog **#17**.