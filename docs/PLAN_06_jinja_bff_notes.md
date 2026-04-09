# Plan 06 — Jinja BFF (tracked notes)

**Full spec:** internal `plan/3.31.26 plans-data loader/06_jinja_bff.md` (may be gitignored). **This file is the in-repo handoff** — update when decisions change.

## Shipped in repo (this branch)

- **Phase 1–2:** `jinja2-fragments` (`Jinja2Blocks`), `jinja-partials` (`register_fastapi_extensions`), runs list HTMX fragment via `runs_page.html` + `block_name=runs_list` + `partials/runs_list_body.html`.
- **Agent OpenAPI:** `GET /openapi-agent.json` — schema filtered to operations tagged `agent` (setup JSON envelope routes, flows JSON/recipe routes, `POST /webhooks/mt`). Point tools here, not at full `/openapi.json`.
- **Phase 3 sample:** `render_partial` for the runs empty state (`partials/runs_list_body.html`).
- **Phase 4 seed:** `dataloader/view_models/runs_list.py` — `runs_list_fragment_context` for the list route.

## Deferred backlog (not in the initial Plan 06 PR)

Schedule these in the **local** `plan/…/02_backlog_priority.md` / `01_cycle_ledger.md` — **not** in this repo. Use the table below so items land with the plan that already owns the surface area.

| Deferred item | Primary plan to attach | When / trigger |
|---------------|------------------------|----------------|
| **Wide `render_partial` rollout** | **06** (Phase 3) | Small PRs alongside template edits; no dedicated cycle jump. |
| **`error_html` / retire `helpers._templates`** | **06** (hygiene §4 in full `06_jinja_bff.md`) | One focused PR after merge; still Plan 06 cleanup. |
| **`docs/sse_contracts.md`** (event names, HTML vs JSON per stream) | **16** (ordering) + **14** (generate/SSE) | When you ship or materially change generate/NL SSE — same workstream as those plans, **not** a generic Jinja task. |
| **Auth-gate `GET /openapi-agent.json`** | **06** (agent contract) + **0** (auth posture) | When the app is anonymously reachable on the public internet **or** when Plan 0 adds cookie/session auth you want mirrored for tooling. |

### Cycle 1 vs Cycle 2 (how to decide)

**This repository does not define cycles** — only maintainer-local ledgers do. Default is: **keep the first three rows in whatever cycle is already doing Plan 06 UI work** (same cycle as the PR you just opened), because they are **template/BFF hygiene**, not NL or generate.

**Move an item to Cycle 2 only if** your local Cycle 2 backlog **already** tracks the sibling plan and that plan is the real driver:

- Prefer **Cycle 2** for **`sse_contracts.md`** *only when* Cycle 2 is where **Plan 14 / NL / generate streaming** already lives (same themes). If those ships in the current cycle, document SSE there in the **same** cycle.
- Do **not** move **`render_partial`**, **`error_html` cleanup**, or **OpenAPI auth** to Cycle 2 **just** to defer them — they fit **Plan 06 / Plan 0** better than a distant cycle unless Cycle 2 is explicitly “auth + public API hardening.”

## Decisions (locked)


| Topic                  | Decision                                                                                                                                                                                                                             |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Agent OpenAPI**      | Ship `**GET /openapi-agent.json`** (or equivalent): **tag-filtered subset only**. Agents, codegen, and LLM tools **must not** use full `**/openapi.json`**. Optional later: auth-gate the filtered endpoint.                         |
| **Template singleton** | One `Jinja2Blocks` / `Jinja2Templates` on `**app.state.templates`**; `main.py` calls `**helpers.set_templates(same_instance)**` until `error_html` is migrated off the module singleton (see full plan § Implementation hygiene §4). |
| **Pilot (default)**    | **Done:** `GET /api/runs` → `runs_page.html` + `block_name=runs_list` + `partials/runs_list_body.html`.                                                                                                                               |


## Before Phase 1 (research)

- **jinja2-fragments** + **jinja_partials:** version matrix vs repo pins; `TemplateResponse(..., block_name=...)`; conflicts with `get_template().render` (`error_html`).
- **HTMX SSE:** mixing HTML `sse-swap` vs JSON events — align with Plan **16**; add `**docs/sse_contracts.md`** when Plan **14** ships.

## Plan 0 Wave A (verify at kickoff)

Repo currently has lifespan + async SQLAlchemy + `**get_db_session`** + `**CurrentAppUserDep**` from `**app.state.default_user_id**` (no auth cookie yet). Re-grep `**async_session_factory**` / `**get_db_session**` on your branch before starting.

## Security (required)

New mutating fragments: same session/auth as sibling routes. When Plan 0 adds cookie auth: pair with SameSite / CSRF — see full plan § **Security**.

## Related

- Plan 0: `plan/.../03_database_plan0.md`
- NL consumer: `plan/.../15_nl_persistence.md`
- Agent JSON twins: `plan/.../10_fund_flows_ui.md`, `14a_field_scoped_ai_hitl.md`, `04_validation_observability.md`