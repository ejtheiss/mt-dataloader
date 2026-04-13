"""Plan 11a Phase 0 — hydrate actor library from legacy config + materialize bindings.

Option A (``11a_shared_actor_library_flow_bindings.md``): keep ``DataLoaderConfig`` /
``flow_compiler`` unchanged; library + bindings project into ``actor_overrides`` before
compose. Imports ``get_base_config_for_generation`` lazily to avoid cycles with
``flows_mutation``.
"""

from __future__ import annotations

from typing import Any


def recipe_flow_ref(emitted_flow_ref: str) -> str:
    """Same semantics as ``dataloader.routers.flows.helpers._recipe_flow_ref``."""
    parts = emitted_flow_ref.rsplit("__", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return emitted_flow_ref


def ensure_actor_library_hydrated_from_legacy(session: Any) -> None:
    """If library + bindings are empty, seed from ``funds_flows`` + ``generation_recipes``.

    Creates stable ``legacy:{recipe_key}:{alias}`` ids so draft round-trips and tools
    can target rows before the Actors registry UI ships.
    """
    if session.actor_library or session.actor_bindings:
        return
    from dataloader.flows_mutation import get_base_config_for_generation

    try:
        base = get_base_config_for_generation(session)
    except (TypeError, ValueError, AttributeError):
        return
    if not getattr(base, "funds_flows", None) or not session.generation_recipes:
        return

    library: list[dict[str, Any]] = []
    bindings: dict[str, dict[str, str]] = {}
    seen_libs: set[str] = set()

    for fc in base.funds_flows:
        rk = recipe_flow_ref(fc.ref)
        if rk not in session.generation_recipes:
            continue
        recipe_dict = session.generation_recipes[rk]
        overrides = recipe_dict.get("actor_overrides") or {}
        bindings.setdefault(rk, {})
        for alias, frame in fc.actors.items():
            lib_id = f"legacy:{rk}:{alias}"
            bindings[rk][alias] = lib_id
            if lib_id in seen_libs:
                continue
            seen_libs.add(lib_id)
            ov = overrides.get(alias) if isinstance(overrides.get(alias), dict) else {}
            entry: dict[str, Any] = {
                "library_actor_id": lib_id,
                "label": f"{alias} · {rk}",
                "frame_type": frame.frame_type,
            }
            ds = ov.get("dataset") if isinstance(ov, dict) else None
            if ds is None:
                ds = frame.dataset
            if ds is not None:
                entry["dataset"] = ds
            et = ov.get("entity_type") if isinstance(ov, dict) else None
            if et is not None:
                entry["entity_type"] = et
            cn = ov.get("customer_name") if isinstance(ov, dict) else None
            if cn is None:
                cn = frame.customer_name
            if cn is not None:
                entry["customer_name"] = cn
            nt = ov.get("name_template") if isinstance(ov, dict) else None
            if nt is None:
                nt = frame.name_template
            if nt is not None:
                entry["name_template"] = nt
            library.append(entry)

    session.actor_library = library
    session.actor_bindings = bindings


def sync_legacy_library_rows_from_recipes(session: Any) -> None:
    """Keep ``legacy:…`` library rows aligned with live ``actor_overrides`` + pattern.

    Without this, the first hydrate snapshot would win and scenario-builder edits to
    ``actor_overrides`` would be overwritten on the next compose.
    """
    if not session.actor_library:
        return
    from dataloader.flows_mutation import get_base_config_for_generation

    try:
        base = get_base_config_for_generation(session)
    except (TypeError, ValueError, AttributeError):
        return
    flow_by_rk: dict[str, Any] = {}
    for fc in getattr(base, "funds_flows", None) or []:
        flow_by_rk[recipe_flow_ref(fc.ref)] = fc

    for row in session.actor_library:
        if not isinstance(row, dict):
            continue
        lid = row.get("library_actor_id")
        if not isinstance(lid, str) or not lid.startswith("legacy:"):
            continue
        parts = lid.split(":", 2)
        if len(parts) != 3:
            continue
        _, rk, alias = parts
        fc = flow_by_rk.get(rk)
        if fc is None or alias not in fc.actors:
            continue
        frame = fc.actors[alias]
        recipe_dict = session.generation_recipes.get(rk) or {}
        overrides = recipe_dict.get("actor_overrides") or {}
        ov = overrides.get(alias) if isinstance(overrides.get(alias), dict) else {}
        row["frame_type"] = frame.frame_type
        ds = ov.get("dataset") if isinstance(ov, dict) else None
        if ds is None:
            ds = frame.dataset
        if ds is not None:
            row["dataset"] = ds
        else:
            row.pop("dataset", None)
        et = ov.get("entity_type") if isinstance(ov, dict) else None
        if et is not None:
            row["entity_type"] = et
        else:
            row.pop("entity_type", None)
        cn = ov.get("customer_name") if isinstance(ov, dict) else None
        if cn is None:
            cn = frame.customer_name
        if cn is not None:
            row["customer_name"] = cn
        else:
            row.pop("customer_name", None)
        nt = ov.get("name_template") if isinstance(ov, dict) else None
        if nt is None:
            nt = frame.name_template
        if nt is not None:
            row["name_template"] = nt
        else:
            row.pop("name_template", None)


def materialize_actor_bindings_to_generation_recipes(session: Any) -> None:
    """Apply ``actor_bindings`` + ``actor_library`` into each recipe's ``actor_overrides``."""
    if not session.actor_bindings:
        return
    library_by_id: dict[str, dict[str, Any]] = {}
    for raw in session.actor_library or []:
        if not isinstance(raw, dict):
            continue
        lid = raw.get("library_actor_id")
        if isinstance(lid, str) and lid:
            library_by_id[lid] = raw

    for recipe_key, recipe_dict in session.generation_recipes.items():
        frame_map = session.actor_bindings.get(recipe_key) or {}
        if not frame_map:
            continue
        merged = dict(recipe_dict.get("actor_overrides") or {})
        for frame, lib_id in frame_map.items():
            row = library_by_id.get(lib_id)
            if not row:
                continue
            patch: dict[str, Any] = {}
            if row.get("dataset") is not None:
                patch["dataset"] = row["dataset"]
            if row.get("entity_type") is not None:
                patch["entity_type"] = row["entity_type"]
            if row.get("customer_name") is not None:
                patch["customer_name"] = row["customer_name"]
            if row.get("name_template") is not None:
                patch["name_template"] = row["name_template"]
            merged[frame] = patch
        recipe_dict["actor_overrides"] = merged
