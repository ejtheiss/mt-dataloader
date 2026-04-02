"""Ref registry, dependency extraction, and resolution."""

from __future__ import annotations

from typing import Iterator

from loguru import logger

from models import DataLoaderConfig, _BaseResourceConfig


class RefRegistry:
    """Typed ref -> UUID store.

    Every key is a typed ref (e.g. ``counterparty.vendor_bob``,
    ``counterparty.vendor_bob.account[0]``).  Every value is a UUID string.
    Baseline resources are pre-seeded before execution.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def register(self, typed_ref: str, resource_id: str) -> None:
        existing = self._store.get(typed_ref)
        if existing is not None and existing != resource_id:
            logger.warning(
                "Ref '{}' already registered (existing: {}, new: {}) — updating",
                typed_ref,
                existing,
                resource_id,
            )
        self._store[typed_ref] = resource_id

    def register_or_update(self, typed_ref: str, resource_id: str) -> None:
        """Register or overwrite an existing ref (used by reconciliation)."""
        self._store[typed_ref] = resource_id

    def unregister(self, typed_ref: str) -> None:
        """Remove a ref (e.g. after user edits so execution creates instead of reusing)."""
        self._store.pop(typed_ref, None)

    def __contains__(self, typed_ref: str) -> bool:
        return typed_ref in self._store

    def resolve(self, value: str) -> str:
        """Resolve a ``$ref:`` string to a UUID.  Literal UUIDs pass through."""
        if not value.startswith("$ref:"):
            return value
        typed_ref = value[5:]
        if typed_ref not in self._store:
            raise KeyError(
                f"Unresolved ref: '{value}'. Available refs: {sorted(self._store.keys())}"
            )
        return self._store[typed_ref]

    def get(self, typed_ref: str) -> str | None:
        return self._store.get(typed_ref)

    def has(self, typed_ref: str) -> bool:
        return typed_ref in self._store

    def snapshot(self) -> dict[str, str]:
        """Immutable copy for manifest serialization."""
        return dict(self._store)


def extract_ref_dependencies(config: _BaseResourceConfig) -> set[str]:
    """Extract all ``$ref:`` dependency targets from a resource config.

    Only populated optional ref fields generate edges — empty/None fields
    are skipped (conditional dependency edges).
    """
    deps: set[str] = set()
    _collect_refs(config.model_dump(exclude_none=True, exclude={"ref"}), deps)
    return deps


def _collect_refs(obj: object, deps: set[str]) -> None:
    """Recursively walk a dict/list collecting ``$ref:`` strings."""
    if isinstance(obj, str) and obj.startswith("$ref:"):
        deps.add(obj[5:])
    elif isinstance(obj, dict):
        for v in obj.values():
            _collect_refs(v, deps)
    elif isinstance(obj, list):
        for item in obj:
            _collect_refs(item, deps)


def resolve_refs(config: _BaseResourceConfig, registry: RefRegistry) -> dict:
    """Dump a config to a dict and resolve all ``$ref:`` strings to UUIDs.

    Returns a dict ready to be passed to the MT SDK (after stripping
    the loader-internal ``ref`` key).  ``display_phase`` and
    ``resource_type`` are ClassVars and are already excluded by
    ``model_dump()``.
    """
    data = config.model_dump(exclude_none=True)
    data.pop("ref", None)
    _resolve_in_place(data, registry)
    _strip_empty_metadata(data)
    return data


def _resolve_in_place(obj: dict | list, registry: RefRegistry) -> None:
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str) and value.startswith("$ref:"):
                obj[key] = registry.resolve(value)
            elif isinstance(value, (dict, list)):
                _resolve_in_place(value, registry)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            if isinstance(item, str) and item.startswith("$ref:"):
                obj[i] = registry.resolve(item)
            elif isinstance(item, (dict, list)):
                _resolve_in_place(item, registry)


def _strip_empty_metadata(obj: dict | list) -> None:
    """Remove ``metadata: {}`` from resolved dicts to keep API payloads clean."""
    if isinstance(obj, dict):
        if "metadata" in obj and obj["metadata"] == {}:
            del obj["metadata"]
        for v in obj.values():
            if isinstance(v, (dict, list)):
                _strip_empty_metadata(v)
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, (dict, list)):
                _strip_empty_metadata(item)


def typed_ref_for(config: _BaseResourceConfig) -> str:
    """Build the typed ref string from a resource config."""
    return f"{config.resource_type}.{config.ref}"


def all_resources(config: DataLoaderConfig) -> Iterator[_BaseResourceConfig]:
    """Yield all resource configs from all sections in declaration order."""
    for field_name in type(config).model_fields:
        value = getattr(config, field_name)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, _BaseResourceConfig):
                yield item
