# Plan 11a — Phase 0 (landed): draft model + hydrate + materialize

This is the **data path** from `plan/.../11a_shared_actor_library_flow_bindings.md` **§4 Option A** before the Actors registry UI (Phase 1) or Band 2 dropdowns (Phase 2).

## What shipped

| Piece | Location |
|-------|----------|
| `LibraryActorEntry` | `models/actor_library.py` |
| `LoaderDraft.actor_library`, `LoaderDraft.actor_bindings` | `models/loader_draft.py` |
| `SessionState.actor_*` | `dataloader/session/__init__.py` |
| Draft round-trip + merge hydrate | `dataloader/session/draft_persist.py` |
| Legacy hydrate + materialize | `dataloader/actor_library_runtime.py` |
| Call before compose | `prepare_actor_library_for_compose` in `dataloader/flows_mutation.py` (`recompose_and_persist_session`, `generate_execute` in `dataloader/routers/flows/api.py`) |
| Config drawer JSON/HTML context | `dataloader/view_models/flows_config_drawer.py` |

## Semantics

- **Bindings:** `actor_bindings[recipe_flow_ref][frame_name] = library_actor_id` (recipe key matches `generation_recipes` / `_recipe_flow_ref`).
- **Hydrate:** When both `actor_library` and `actor_bindings` are empty but `generation_recipes` + authoring `funds_flows` exist, synthetic ids `legacy:{recipe_key}:{alias}` are created so old drafts round-trip without losing actor identity.
- **Materialize:** Before `compose_all_recipes`, bound frames get `actor_overrides` rebuilt from library rows (compiler unchanged).

## Next (Phases 1–2 from 11a plan)

- Always-visible **Actors** band on `GET /flows`, CRUD library rows, persist via existing draft hooks.
- **Band 2** binding dropdown + `POST` to update `actor_bindings` and recompose.
