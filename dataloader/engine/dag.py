"""Topological DAG construction, legal-entity injection, and dry-run batching."""

from __future__ import annotations

from graphlib import TopologicalSorter
from typing import Any

from loguru import logger

from dataloader.staged_fire import FIREABLE_TYPES
from models import DataLoaderConfig, _BaseResourceConfig
from models.config import legal_entity_omit_connection_id_on_create

from .refs import (
    RefRegistry,
    all_resources,
    extract_ref_dependencies,
    typed_ref_for,
)

_FIAT_IA_CURRENCIES: frozenset[str] = frozenset({"USD", "CAD"})


def build_dag(
    config: DataLoaderConfig,
) -> tuple[TopologicalSorter, dict[str, _BaseResourceConfig]]:
    """Build a ``TopologicalSorter`` from the config's dependency graph.

    Returns the sorter (**unprepared**) and a map from typed_ref -> config.
    Baseline refs that appear as dependencies are auto-added by graphlib
    as nodes with no predecessors.

    Child refs (e.g. ``counterparty.vendor_bob.account[0]``) get an
    implicit edge to their parent (``counterparty.vendor_bob``), ensuring
    the parent is created before any resource that depends on the child.

    **Legal entities** always depend on **every** configured ``connection`` so
    execution never creates LEs in the same batch as (or before) connections.
    MT ties LEs to connections (``connection_id`` / Connection Legal Entity);
    parallel LE + connection creates produced confusing failures and log order.
    """
    ts: TopologicalSorter[str] = TopologicalSorter()
    resource_map: dict[str, _BaseResourceConfig] = {}

    connection_refs = [typed_ref_for(c) for c in config.connections]

    for resource in all_resources(config):
        ref = typed_ref_for(resource)
        deps = extract_ref_dependencies(resource)
        for explicit_dep in resource.depends_on:
            if explicit_dep.startswith("$ref:"):
                deps.add(explicit_dep[5:])
        if resource.resource_type == "legal_entity" and connection_refs:
            deps.update(connection_refs)
        expanded = set(deps)
        for dep in deps:
            parts = dep.split(".")
            if len(parts) >= 3:
                parent = f"{parts[0]}.{parts[1]}"
                expanded.add(parent)
        ts.add(ref, *expanded)
        resource_map[ref] = resource

    return ts, resource_map


def inject_legal_entity_psp_connection_id(
    config: DataLoaderConfig,
    registry: RefRegistry,
    resolved: dict[str, Any],
    *,
    typed_ref: str,
) -> None:
    """Fill ``connection_id`` on legal-entity **create** when absent (PSP only).

    When there is exactly one ``connections[]`` row and it is ``modern_treasury``,
    we omit ``connection_id`` on LE create (MT infers it). Any value is stripped
    from *resolved*.

    If JSON omits ``connection_id`` and there are **multiple** connections with
    ``modern_treasury``, prefer the UUID for **this** legal entity's **fiat
    (USD/CAD) internal account** connection — MT Connection Legal Entity flows
    align with the bank/fiat rail, not the first row in ``connections[]`` (which
    breaks when there are two ``modern_treasury`` refs or list order differs).

    Falls back to the first registered ``modern_treasury`` connection if the LE
    has no matching IAs yet. BYOB-only configs are unchanged.

    Mutates *resolved* in place, analogous to sandbox mock data on LE payloads.
    """
    if legal_entity_omit_connection_id_on_create(config):
        resolved.pop("connection_id", None)
        return

    if resolved.get("connection_id"):
        return

    le_ref_target = f"$ref:{typed_ref}"

    def _conn_row_entity_id(conn_tref: str) -> str | None:
        for c in config.connections:
            if typed_ref_for(c) == conn_tref:
                return c.entity_id
        return None

    ias_for_le = [ia for ia in config.internal_accounts if ia.legal_entity_id == le_ref_target]
    # Fiat IAs first (CLE / bank rail), then any other IA on this LE.
    ias_ordered = sorted(
        ias_for_le,
        key=lambda ia: 0 if ia.currency in _FIAT_IA_CURRENCIES else 1,
    )
    for ia in ias_ordered:
        cid_str = ia.connection_id
        if not isinstance(cid_str, str) or not cid_str.startswith("$ref:connection."):
            continue
        conn_tref = cid_str[5:]  # strip "$ref:"
        if _conn_row_entity_id(conn_tref) != "modern_treasury":
            continue
        cid = registry.get(conn_tref)
        if cid:
            resolved["connection_id"] = cid
            logger.debug(
                "Injected connection_id for {} from {} (via IA {}) → {}…",
                typed_ref,
                conn_tref,
                ia.ref,
                cid[:12],
            )
            return

    for conn in config.connections:
        if conn.entity_id != "modern_treasury":
            continue
        tref = typed_ref_for(conn)
        cid = registry.get(tref)
        if cid:
            resolved["connection_id"] = cid
            logger.debug(
                "Injected connection_id for {} from {} (fallback) → {}…",
                typed_ref,
                tref,
                cid[:12],
            )
            return


def dry_run(
    config: DataLoaderConfig,
    known_refs: set[str] | None = None,
    skip_refs: set[str] | None = None,
) -> list[list[str]]:
    """Compute execution order without running anything.

    Returns a list of batches where each batch is a list of typed refs
    that can execute concurrently.  Known refs (from org discovery) are
    used to validate ``$ref:`` targets; skip refs are filtered from
    batches (pre-existing, not created).

    Raises ``CycleError`` if the config has circular dependencies.
    Raises ``KeyError`` if a ``$ref:`` target doesn't exist in config
    or known refs.
    """
    ts, resource_map = build_dag(config)
    ts.prepare()

    all_known_refs = set(resource_map.keys())
    if known_refs:
        all_known_refs |= known_refs

    def _is_known_or_child(dep: str) -> bool:
        """A ref is resolvable if it exists directly, or if its parent
        (type.key) exists and the ref has a child selector (.account[0], etc.).
        Child refs are auto-registered at runtime by handlers."""
        if dep in all_known_refs:
            return True
        parts = dep.split(".")
        if len(parts) >= 3:
            parent = f"{parts[0]}.{parts[1]}"
            return parent in all_known_refs
        return False

    for ref, resource in resource_map.items():
        for dep in extract_ref_dependencies(resource):
            if not _is_known_or_child(dep):
                raise KeyError(
                    f"Unresolvable ref '$ref:{dep}' in resource '{ref}'. "
                    f"It must be defined in the config."
                )

    for ref, resource in resource_map.items():
        for dep_str in resource.depends_on:
            if dep_str.startswith("$ref:"):
                dep = dep_str[5:]
                if not _is_known_or_child(dep):
                    raise KeyError(
                        f"Unresolvable depends_on ref '$ref:{dep}' in "
                        f"resource '{ref}'. It must be defined in the "
                        f"config."
                    )

    staged_refs = {
        ref for ref, resource in resource_map.items() if getattr(resource, "staged", False)
    }
    for ref in staged_refs:
        rtype = resource_map[ref].resource_type
        if rtype not in FIREABLE_TYPES:
            logger.warning(
                "Staged resource '{}' has type '{}' which cannot be fired. Fireable types: {}",
                ref,
                rtype,
                ", ".join(sorted(FIREABLE_TYPES)),
            )
    if staged_refs:

        def _dep_hits_staged(dep: str) -> str | None:
            if dep in staged_refs:
                return dep
            parts = dep.split(".")
            if len(parts) >= 3:
                parent = f"{parts[0]}.{parts[1]}"
                if parent in staged_refs:
                    return parent
            return None

        for ref, resource in resource_map.items():
            if ref in staged_refs:
                for dep in extract_ref_dependencies(resource):
                    hit = _dep_hits_staged(dep)
                    if hit:
                        raise ValueError(
                            f"Staged resource '{ref}' has a data-field "
                            f"$ref to staged resource '{hit}' (via "
                            f"'{dep}'). Data-field refs between staged "
                            f"resources cannot resolve at execution time "
                            f"because staged resources have no created_id. "
                            f"Either un-stage '{hit}' or remove the $ref."
                        )
            else:
                all_deps = extract_ref_dependencies(resource)
                for dep_str in resource.depends_on:
                    if dep_str.startswith("$ref:"):
                        all_deps.add(dep_str[5:])
                for dep in all_deps:
                    hit = _dep_hits_staged(dep)
                    if hit:
                        raise ValueError(
                            f"Resource '{ref}' depends on staged resource "
                            f"'{hit}' (via '{dep}'). Either un-stage "
                            f"'{hit}' or also stage '{ref}'."
                        )

    _skip = skip_refs or set()
    batches: list[list[str]] = []
    while ts.is_active():
        ready = ts.get_ready()
        to_create = [r for r in ready if r in resource_map and r not in _skip]
        auto_done = [r for r in ready if r not in resource_map or r in _skip]
        if auto_done:
            ts.done(*auto_done)
        if to_create:
            batches.append(to_create)
            ts.done(*to_create)

    return batches
