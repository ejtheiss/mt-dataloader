# Plan 05 — Server config authority (tracked summary)

**Normative detail:** internal plan `plan/3.31.26 plans-data loader/05_server_config_authority.md` (not in git). This file records **what shipped in-repo** and **closure status** for engineering handoff.

**Last reconciled:** 2026-04-07.

## Shipped (v1 scope)

| Item | Location / notes |
|------|------------------|
| HTMX loader errors | `loader_validation_failure_htmx_parts` + `_pipeline_error_response` in `dataloader/routers/setup.py` |
| Scenario snapshot | `POST /api/flows/scenario-snapshot` |
| Recipe patch | `POST /api/flows/recipe-patch` (merge + validate + recompose) |
| Config patch | `POST /api/config/patch-json` — `shallow_merge` + ordered **`pointer_sets`** (RFC 6901 set via `dataloader/json_pointer.py`) → `run_loader_validation_pipeline` |
| Fund Flows deep link | `?flow=&panel=config` (+ `generated` where used); legacy `open_scale` still stripped in `templates/flows.html` for compatibility |
| Scenario builder apply | `static/js/scenario-builder.js` **`genApply`** → **`recipe-patch`** with `{ flow_ref, patch: full buildRecipe() }` |
| Recompose authority | `dataloader/flows_mutation.py` — `compose_all_recipes`, `recompose_and_persist_session`, merge helpers; `dataloader/routers/flows.py` imports only |
| AuthoringConfig | `AuthoringConfig.from_json(raw_bytes)` in `dataloader/loader_validation.py` (no duplicate constructor) |

## Tests

- `tests/test_flows_plan05_api.py`, `tests/test_loader_setup_json_api.py` (patch-json + pointers), `tests/test_json_pointer.py`, `tests/test_flow_actor_config.py` (stubs `flows_mutation`).

## Explicitly deferred (not blocking Plan 05 v1)

- **Phase 4:** `PhaseRecord` / macro trace export from the shared runner.
- **Phase 5 / Tier B:** pass-level DAG traces beyond current diagnostics.
- **Tier A aggressive:** removing `buildRecipe` / DOM-assembled recipe in favor of many small server commands + fragments (see plan § Tier A — requires follow-up PRs).
- **`POST /api/flows/recipe-to-working-config`:** retained for agents/tools that POST a full recipe body; UI apply uses **`recipe-patch`**.

## Definition of done (v1)

All items satisfied for the **v1** slice described in the parent plan: shared loader pipeline, § v1 JSON + HTMX parity for loader failures, centralized flow recomposition (`flows_mutation`), patch-json with shallow + pointer sets, scenario apply through recipe-patch, `AuthoringConfig.from_json` for raw bytes.
