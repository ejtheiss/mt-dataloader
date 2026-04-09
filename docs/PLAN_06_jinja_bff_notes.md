# Plan 06 — Jinja BFF (tracked notes)

**Full spec:** internal `plan/3.31.26 plans-data loader/06_jinja_bff.md` (may be gitignored). **This file is the in-repo handoff** — update when decisions change.

## Shipped in repo (this branch)

- **Phase 1–2:** `jinja2-fragments` (`Jinja2Blocks`), `jinja-partials` (`register_fastapi_extensions`), runs list HTMX fragment via `runs_page.html` + `block_name=runs_list` + `partials/runs_list_body.html`.
- **Agent OpenAPI:** `GET /openapi-agent.json` — schema filtered to operations tagged `agent` (setup JSON envelope routes, flows JSON/recipe routes, `POST /webhooks/mt`). Point tools here, not at full `/openapi.json`.
- **Phase 3 sample:** `render_partial` for the runs empty state (`partials/runs_list_body.html`).
- **Phase 4 seed:** `dataloader/view_models/runs_list.py` — `runs_list_fragment_context` for the list route.

**Still optional later:** wide `render_partial` rollout, `error_html` → `request.app.state.templates` / `TemplatesDep`, `docs/sse_contracts.md`, auth-gated agent OpenAPI.

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