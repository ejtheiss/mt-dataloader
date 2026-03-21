"""Execution engine for the Modern Treasury Dataloader.

Owns four responsibilities:
1. RefRegistry — typed ref → UUID store
2. Ref resolver — extracts dependency edges, resolves $ref: strings to UUIDs
3. DAG executor — graphlib.TopologicalSorter with asyncio.TaskGroup concurrency
4. Run manifest — incremental JSON writer for resume/cleanup/audit

Zero SDK dependency — handlers are received as a dispatch table.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterator

from graphlib import TopologicalSorter

from models import (
    DataLoaderConfig,
    FailedEntry,
    HandlerResult,
    ManifestEntry,
    _BaseResourceConfig,
)

if TYPE_CHECKING:
    pass

__all__ = [
    "RefRegistry",
    "extract_ref_dependencies",
    "resolve_refs",
    "typed_ref_for",
    "all_resources",
    "build_dag",
    "dry_run",
    "generate_run_id",
    "execute",
    "config_hash",
    "RunManifest",
]

# ---------------------------------------------------------------------------
# Type aliases for handler dispatch
# ---------------------------------------------------------------------------

HandlerFn = Callable[..., Awaitable[HandlerResult]]
EmitFn = Callable[[str, str, dict[str, Any]], Awaitable[None]]
DisconnectCheckFn = Callable[[], bool]
ResourceCreatedFn = Callable[[str, str, str], None]

# ---------------------------------------------------------------------------
# RefRegistry
# ---------------------------------------------------------------------------


class RefRegistry:
    """Typed ref -> UUID store.

    Every key is a typed ref (e.g. ``counterparty.vendor_bob``,
    ``counterparty.vendor_bob.account[0]``).  Every value is a UUID string.
    Baseline resources are pre-seeded before execution.
    """

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def register(self, typed_ref: str, resource_id: str) -> None:
        if typed_ref in self._store:
            raise ValueError(
                f"Ref '{typed_ref}' already registered "
                f"(existing: {self._store[typed_ref]}, new: {resource_id})"
            )
        self._store[typed_ref] = resource_id

    def resolve(self, value: str) -> str:
        """Resolve a ``$ref:`` string to a UUID.  Literal UUIDs pass through."""
        if not value.startswith("$ref:"):
            return value
        typed_ref = value[5:]
        if typed_ref not in self._store:
            raise KeyError(
                f"Unresolved ref: '{value}'. "
                f"Available refs: {sorted(self._store.keys())}"
            )
        return self._store[typed_ref]

    def get(self, typed_ref: str) -> str | None:
        return self._store.get(typed_ref)

    def has(self, typed_ref: str) -> bool:
        return typed_ref in self._store

    def snapshot(self) -> dict[str, str]:
        """Immutable copy for manifest serialization."""
        return dict(self._store)


# ---------------------------------------------------------------------------
# Ref extraction & resolution
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# DAG building
# ---------------------------------------------------------------------------


def typed_ref_for(config: _BaseResourceConfig) -> str:
    """Build the typed ref string from a resource config."""
    return f"{config.resource_type}.{config.ref}"


def all_resources(config: DataLoaderConfig) -> Iterator[_BaseResourceConfig]:
    """Yield all resource configs from all sections in declaration order."""
    for field_name in config.model_fields:
        value = getattr(config, field_name)
        if not isinstance(value, list):
            continue
        for item in value:
            if isinstance(item, _BaseResourceConfig):
                yield item


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
    """
    ts: TopologicalSorter[str] = TopologicalSorter()
    resource_map: dict[str, _BaseResourceConfig] = {}

    for resource in all_resources(config):
        ref = typed_ref_for(resource)
        deps = extract_ref_dependencies(resource)
        for explicit_dep in resource.depends_on:
            if explicit_dep.startswith("$ref:"):
                deps.add(explicit_dep[5:])
        expanded = set(deps)
        for dep in deps:
            parts = dep.split(".")
            if len(parts) >= 3:
                parent = f"{parts[0]}.{parts[1]}"
                expanded.add(parent)
        ts.add(ref, *expanded)
        resource_map[ref] = resource

    return ts, resource_map


def dry_run(
    config: DataLoaderConfig,
    baseline_refs: set[str] | None = None,
) -> list[list[str]]:
    """Compute execution order without running anything.

    Returns a list of batches where each batch is a list of typed refs
    that can execute concurrently.  Baseline refs are filtered out of the
    batches (they are pre-existing, not created).

    Raises ``CycleError`` if the config has circular dependencies.
    Raises ``KeyError`` if a ``$ref:`` target doesn't exist in baseline
    or config.
    """
    ts, resource_map = build_dag(config)
    ts.prepare()

    all_known_refs = set(resource_map.keys())
    if baseline_refs:
        all_known_refs |= baseline_refs

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
                    f"It must be defined in the config or baseline.yaml."
                )

    for ref, resource in resource_map.items():
        for dep_str in resource.depends_on:
            if dep_str.startswith("$ref:"):
                dep = dep_str[5:]
                if not _is_known_or_child(dep):
                    raise KeyError(
                        f"Unresolvable depends_on ref '$ref:{dep}' in "
                        f"resource '{ref}'. It must be defined in the "
                        f"config or baseline."
                    )

    batches: list[list[str]] = []
    while ts.is_active():
        ready = ts.get_ready()
        to_create = [r for r in ready if r in resource_map]
        baseline = [r for r in ready if r not in resource_map]
        if baseline:
            ts.done(*baseline)
        if to_create:
            batches.append(to_create)
            ts.done(*to_create)

    return batches


# ---------------------------------------------------------------------------
# Run ID & config hash
# ---------------------------------------------------------------------------


def generate_run_id() -> str:
    """``YYYYMMDDTHHMMSS_<8-char-hex>``, filesystem-safe."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    suffix = secrets.token_hex(4)
    return f"{ts}_{suffix}"


def config_hash(config: DataLoaderConfig) -> str:
    """SHA-256 of the canonical JSON serialization of the config."""
    canonical = config.model_dump_json(exclude_none=True)
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


# ---------------------------------------------------------------------------
# Run manifest
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RunManifest:
    """Mutable manifest accumulator.  Written incrementally during execution."""

    run_id: str
    config_hash: str
    started_at: str = field(default_factory=_now_iso)
    completed_at: str | None = None
    status: str = "running"
    resources_created: list[ManifestEntry] = field(default_factory=list)
    resources_failed: list[FailedEntry] = field(default_factory=list)

    def record(self, entry: ManifestEntry) -> None:
        self.resources_created.append(entry)

    def record_failure(self, typed_ref: str, error: str) -> None:
        self.resources_failed.append(
            FailedEntry(typed_ref=typed_ref, error=error, failed_at=_now_iso())
        )

    def finalize(self, status: str) -> None:
        self.status = status
        self.completed_at = _now_iso()

    def write(self, runs_dir: str) -> Path:
        """Write manifest to ``runs/<run_id>.json``.  Creates dir if needed."""
        dirpath = Path(runs_dir)
        dirpath.mkdir(parents=True, exist_ok=True)
        file_path = dirpath / f"{self.run_id}.json"
        file_path.write_text(
            json.dumps(self._to_dict(), indent=2, default=str),
            encoding="utf-8",
        )
        return file_path

    def verify_hash(self, config: DataLoaderConfig) -> bool:
        """Check that a config matches the hash recorded at run start."""
        return self.config_hash == config_hash(config)

    @classmethod
    def load(cls, path: str | Path) -> RunManifest:
        """Load a manifest from a JSON file for resume or cleanup."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        manifest = cls(
            run_id=data["run_id"],
            config_hash=data["config_hash"],
            started_at=data["started_at"],
            completed_at=data.get("completed_at"),
            status=data["status"],
        )
        for entry_data in data.get("resources_created", []):
            manifest.resources_created.append(ManifestEntry(**entry_data))
        for fail_data in data.get("resources_failed", []):
            manifest.resources_failed.append(FailedEntry(**fail_data))
        return manifest

    def _to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "config_hash": self.config_hash,
            "resources_created": [
                {
                    "batch": e.batch,
                    "resource_type": e.resource_type,
                    "typed_ref": e.typed_ref,
                    "created_id": e.created_id,
                    "created_at": e.created_at,
                    "deletable": e.deletable,
                    "cleanup_status": e.cleanup_status,
                }
                for e in self.resources_created
            ],
            "resources_failed": [
                {
                    "typed_ref": f.typed_ref,
                    "error": f.error,
                    "failed_at": f.failed_at,
                }
                for f in self.resources_failed
            ],
        }


# ---------------------------------------------------------------------------
# DAG executor
# ---------------------------------------------------------------------------


async def execute(
    config: DataLoaderConfig,
    registry: RefRegistry,
    handler_dispatch: dict[str, HandlerFn],
    run_id: str,
    semaphore: asyncio.Semaphore,
    emit_sse: EmitFn,
    is_disconnected: DisconnectCheckFn,
    runs_dir: str = "runs",
    on_resource_created: ResourceCreatedFn | None = None,
) -> RunManifest:
    """Execute the DAG with intra-batch concurrency.

    Baseline refs (in the registry but not in the config) are auto-drained
    from each batch.  Uses ``asyncio.TaskGroup`` for proper cancellation
    of sibling tasks on failure.
    """
    ts, resource_map = build_dag(config)
    ts.prepare()

    manifest = RunManifest(
        run_id=run_id,
        config_hash=config_hash(config),
    )

    batch_index = 0

    try:
        while ts.is_active():
            if is_disconnected():
                manifest.finalize("disconnected")
                manifest.write(runs_dir)
                return manifest

            ready = ts.get_ready()

            baseline = [r for r in ready if r not in resource_map]
            to_create = [r for r in ready if r in resource_map]

            if baseline:
                ts.done(*baseline)
            if not to_create:
                continue

            async def create_one(typed_ref: str, _batch: int) -> None:
                resource = resource_map[typed_ref]
                await emit_sse("creating", typed_ref, {})

                async with semaphore:
                    resolved = resolve_refs(resource, registry)
                    handler = handler_dispatch[resource.resource_type]
                    result = await handler(
                        resolved,
                        idempotency_key=f"{run_id}:{typed_ref}",
                        typed_ref=typed_ref,
                    )

                registry.register(typed_ref, result.created_id)
                for child_key, child_id in result.child_refs.items():
                    registry.register(f"{typed_ref}.{child_key}", child_id)

                if on_resource_created:
                    on_resource_created(run_id, result.created_id, typed_ref)
                    for child_key, child_id in result.child_refs.items():
                        on_resource_created(
                            run_id, child_id, f"{typed_ref}.{child_key}"
                        )

                manifest.record(
                    ManifestEntry(
                        batch=_batch,
                        resource_type=result.resource_type,
                        typed_ref=typed_ref,
                        created_id=result.created_id,
                        created_at=_now_iso(),
                        deletable=result.deletable,
                    )
                )
                manifest.write(runs_dir)
                await emit_sse(
                    "created",
                    typed_ref,
                    {"id": result.created_id, "child_refs": result.child_refs},
                )

            try:
                async with asyncio.TaskGroup() as tg:
                    for ref in to_create:
                        tg.create_task(create_one(ref, batch_index))
            except* Exception as eg:
                first = eg.exceptions[0]
                failed_ref = _guess_failed_ref(first, to_create, resource_map)
                error_detail = _format_exception_detail(first, failed_ref)
                manifest.record_failure(failed_ref, error_detail)
                manifest.finalize("failed")
                manifest.write(runs_dir)
                raise first from None

            ts.done(*to_create)
            batch_index += 1

    except asyncio.CancelledError:
        manifest.finalize("disconnected")
        manifest.write(runs_dir)
        return manifest

    manifest.finalize("completed")
    manifest.write(runs_dir)
    return manifest


def _guess_failed_ref(
    exc: BaseException,
    batch_refs: list[str],
    resource_map: dict[str, _BaseResourceConfig],
) -> str:
    """Best-effort attempt to identify which ref caused an exception."""
    msg = str(exc)
    for ref in batch_refs:
        if ref in msg:
            return ref
    return batch_refs[0] if batch_refs else "unknown"


def _format_exception_detail(exc: BaseException, failed_ref: str) -> str:
    """Extract a human-readable error string, enriching APIStatusError with body details."""
    try:
        from modern_treasury._exceptions import APIStatusError
    except ImportError:
        return f"{type(exc).__name__}: {exc}"

    if isinstance(exc, APIStatusError):
        body = exc.body
        if isinstance(body, dict):
            errors = body.get("errors", body)
            msg = errors.get("message", str(errors)) if isinstance(errors, dict) else str(errors)
            return f"[{failed_ref}] HTTP {exc.status_code}: {msg}"
        return f"[{failed_ref}] HTTP {exc.status_code}: {body}"
    return f"[{failed_ref}] {type(exc).__name__}: {exc}"
