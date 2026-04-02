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
import re
import secrets
from datetime import datetime, timezone
from graphlib import TopologicalSorter
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator

from loguru import logger

from jsonutil import dumps_pretty
from models import (
    DataLoaderConfig,
    HandlerResult,
    ManifestEntry,
    RunManifest,
    _BaseResourceConfig,
)
from models.config import legal_entity_omit_connection_id_on_create
from models.shared import ErrorStrategy


def _extract_display_name(resource: _BaseResourceConfig) -> str:
    """Lazy wrapper to avoid circular import with helpers."""
    from helpers import extract_display_name

    return extract_display_name(resource)


def _register_run_org_for_webhooks(run_id: str, org_id: str | None) -> None:
    """Tell the webhook layer which MT org owns a run (for listener filtering)."""
    if not org_id:
        return
    import webhooks as wh_mod

    wh_mod.register_run_org(run_id, org_id)


class ExecutionPhaseError(Exception):
    """Failure during DAG execution; carries ``typed_ref`` through ``TaskGroup`` / ``ExceptionGroup``.

    Raised as ``raise ExecutionPhaseError(typed_ref) from exc`` so the original
    API error remains on ``__cause__`` for formatting (e.g. ``APIStatusError``).
    """

    __slots__ = ("typed_ref",)

    def __init__(self, typed_ref: str) -> None:
        self.typed_ref = typed_ref
        super().__init__(f"execution failed for ref {typed_ref!r}")


__all__ = [
    "ExecutionPhaseError",
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
    "list_manifest_ids",
    "_now_iso",
    "inject_legal_entity_psp_connection_id",
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
    for field_name in type(config).model_fields:
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


_FIAT_IA_CURRENCIES: frozenset[str] = frozenset({"USD", "CAD"})


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

    from webhooks import FIREABLE_TYPES

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
# Run manifest — ``RunManifest`` is ``models.manifest.RunManifest`` (Pydantic).
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# DAG executor
# ---------------------------------------------------------------------------


async def _execute_with_error_strategy(
    *,
    resource: _BaseResourceConfig,
    resolved: dict,
    typed_ref: str,
    run_id: str,
    handler_dispatch: dict[str, HandlerFn],
    update_refs: dict[str, str],
    update_dispatch: dict[str, HandlerFn],
    emit_sse: EmitFn,
    resource_map: dict[str, _BaseResourceConfig],
    registry: RefRegistry,
) -> HandlerResult:
    """Execute a single resource creation with on_error strategy handling."""
    strategy: ErrorStrategy = getattr(resource, "on_error", None) or ErrorStrategy()

    async def _do_create() -> HandlerResult:
        if typed_ref in update_refs and resource.resource_type in update_dispatch:
            handler = update_dispatch[resource.resource_type]
            return await handler(
                resolved,
                resource_id=update_refs[typed_ref],
                idempotency_key=f"{run_id}:{typed_ref}",
                typed_ref=typed_ref,
            )
        handler = handler_dispatch[resource.resource_type]
        return await handler(
            resolved,
            idempotency_key=f"{run_id}:{typed_ref}",
            typed_ref=typed_ref,
        )

    if strategy.action == "fail":
        return await _do_create()

    if strategy.action == "skip":
        try:
            return await _do_create()
        except Exception as exc:
            logger.log(strategy.log_level.upper(), "Skipping {} after error: {}", typed_ref, exc)
            await emit_sse("skipped", typed_ref, {"error": str(exc)})
            return HandlerResult(
                created_id="SKIPPED", resource_type=resource.resource_type, deletable=False
            )

    if strategy.action == "retry":
        n = strategy.max_retries
        if n < 1:
            return await _do_create()
        last_exc: Exception | None = None
        for attempt in range(1, n + 1):
            try:
                return await _do_create()
            except Exception as exc:
                last_exc = exc
                logger.log(
                    strategy.log_level.upper(),
                    "Retry {}/{} for {} after: {}",
                    attempt,
                    n,
                    typed_ref,
                    exc,
                )
                await emit_sse("retrying", typed_ref, {"attempt": attempt, "error": str(exc)})
                if attempt < n:
                    await asyncio.sleep(strategy.retry_delay_seconds)
        assert last_exc is not None
        raise last_exc

    if strategy.action == "substitute" and strategy.substitute_ref:
        try:
            return await _do_create()
        except Exception as exc:
            logger.log(
                strategy.log_level.upper(),
                "Substituting {} with {} after: {}",
                typed_ref,
                strategy.substitute_ref,
                exc,
            )
            await emit_sse(
                "substituting",
                typed_ref,
                {
                    "substitute": strategy.substitute_ref,
                    "error": str(exc),
                },
            )
            sub_ref = strategy.substitute_ref
            if sub_ref.startswith("$ref:"):
                sub_ref = sub_ref[5:]
            sub_resource = resource_map.get(sub_ref)
            if not sub_resource:
                raise RuntimeError(
                    f"Substitute ref '{strategy.substitute_ref}' not found in config"
                ) from exc
            sub_resolved = resolve_refs(sub_resource, registry)
            sub_handler = handler_dispatch[sub_resource.resource_type]
            return await sub_handler(
                sub_resolved,
                idempotency_key=f"{run_id}:{sub_ref}",
                typed_ref=sub_ref,
            )

    return await _do_create()


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
    skip_refs: set[str] | None = None,
    update_refs: dict[str, str] | None = None,
    update_dispatch: dict[str, HandlerFn] | None = None,
    *,
    mt_org_id: str | None = None,
    mt_org_label: str | None = None,
) -> RunManifest:
    """Execute the DAG with intra-batch concurrency.

    Baseline refs (in the registry but not in the config) are auto-drained
    from each batch.  Uses ``asyncio.TaskGroup`` for proper cancellation
    of sibling tasks on failure.

    Resources in *update_refs* are routed through *update_dispatch* with
    the existing resource ID so they call ``.update()`` instead of
    ``.create()``.
    """
    _skip = skip_refs or set()
    _update = update_refs or {}
    _update_dispatch = update_dispatch or {}
    ts, resource_map = build_dag(config)
    ts.prepare()

    manifest = RunManifest(
        run_id=run_id,
        config_hash=config_hash(config),
        mt_org_id=mt_org_id,
        mt_org_label=mt_org_label,
    )
    if mt_org_id:
        _register_run_org_for_webhooks(run_id, mt_org_id)
    staged_payloads: dict[str, dict] = {}

    batch_index = 0

    try:
        while ts.is_active():
            if is_disconnected():
                manifest.finalize("disconnected")
                manifest.write(runs_dir)
                return manifest

            ready = ts.get_ready()

            baseline = [r for r in ready if r not in resource_map or r in _skip]
            to_create = [r for r in ready if r in resource_map and r not in _skip]

            if baseline:
                ts.done(*baseline)
            if not to_create:
                continue

            async def create_one(typed_ref: str, _batch: int) -> None:
                resource = resource_map[typed_ref]
                dn = _extract_display_name(resource)
                await emit_sse("creating", typed_ref, {"display_name": dn} if dn else {})

                try:
                    async with semaphore:
                        resolved = resolve_refs(resource, registry)
                        if resource.resource_type == "legal_entity":
                            inject_legal_entity_psp_connection_id(
                                config,
                                registry,
                                resolved,
                                typed_ref=typed_ref,
                            )

                        if getattr(resource, "staged", False):
                            staged_payloads[typed_ref] = resolved
                            manifest.record_staged(typed_ref, resource.resource_type)
                            manifest.write(runs_dir)
                            await emit_sse("staged", typed_ref, {"display_name": dn} if dn else {})
                            return

                        result = await _execute_with_error_strategy(
                            resource=resource,
                            resolved=resolved,
                            typed_ref=typed_ref,
                            run_id=run_id,
                            handler_dispatch=handler_dispatch,
                            update_refs=_update,
                            update_dispatch=_update_dispatch,
                            emit_sse=emit_sse,
                            resource_map=resource_map,
                            registry=registry,
                        )
                except Exception as exc:
                    raise ExecutionPhaseError(typed_ref) from exc

                registry.register(typed_ref, result.created_id)
                for child_key, child_id in result.child_refs.items():
                    registry.register(f"{typed_ref}.{child_key}", child_id)

                if on_resource_created:
                    on_resource_created(run_id, result.created_id, typed_ref)
                    for child_key, child_id in result.child_refs.items():
                        on_resource_created(run_id, child_id, f"{typed_ref}.{child_key}")

                manifest.record(
                    ManifestEntry(
                        batch=_batch,
                        resource_type=result.resource_type,
                        typed_ref=typed_ref,
                        created_id=result.created_id,
                        created_at=_now_iso(),
                        deletable=result.deletable,
                        child_refs=result.child_refs,
                    )
                )
                manifest.write(runs_dir)
                data: dict[str, Any] = {"id": result.created_id, "child_refs": result.child_refs}
                if dn:
                    data["display_name"] = dn
                await emit_sse("created", typed_ref, data)

            try:
                async with asyncio.TaskGroup() as tg:
                    for ref in to_create:
                        tg.create_task(create_one(ref, batch_index))
            except* Exception as eg:
                for exc in eg.exceptions:
                    eph = _find_execution_phase_error(exc)
                    if eph is not None:
                        failed_ref = eph.typed_ref
                        leaf: BaseException = eph.__cause__ if eph.__cause__ is not None else eph
                    else:
                        leaf = exc
                        failed_ref = _guess_failed_ref(leaf, to_create, resource_map)
                    error_detail = _format_exception_detail(leaf, failed_ref)
                    manifest.record_failure(failed_ref, error_detail)
                    await emit_sse("error", failed_ref, {"error": error_detail})
                manifest.finalize("failed")
                manifest.write(runs_dir)
                raise eg.exceptions[0] from None

            ts.done(*to_create)
            batch_index += 1

    except asyncio.CancelledError:
        manifest.finalize("disconnected")
        manifest.write(runs_dir)
        _write_staged_payloads(staged_payloads, runs_dir, run_id)
        return manifest

    manifest.finalize("completed")
    manifest.write(runs_dir)
    _write_staged_payloads(staged_payloads, runs_dir, run_id)

    return manifest


def _write_staged_payloads(staged_payloads: dict[str, dict], runs_dir: str, run_id: str) -> None:
    if not staged_payloads:
        return
    staged_path = Path(runs_dir) / f"{run_id}_staged.json"
    staged_path.write_text(dumps_pretty(staged_payloads), encoding="utf-8")


def _find_execution_phase_error(exc: BaseException) -> ExecutionPhaseError | None:
    """Find ``ExecutionPhaseError`` in an ``ExceptionGroup`` or ``__cause__`` chain."""
    if isinstance(exc, ExecutionPhaseError):
        return exc
    nested = getattr(exc, "exceptions", None)
    if nested:
        for sub in nested:
            found = _find_execution_phase_error(sub)
            if found is not None:
                return found
    cause = exc.__cause__
    if isinstance(cause, BaseException):
        return _find_execution_phase_error(cause)
    return None


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
            if isinstance(errors, dict):
                msg = errors.get("message", str(errors))
                param = errors.get("parameter")
                code = errors.get("code")
                if param:
                    msg = f"{msg} (parameter: {param})"
                if code and code not in str(msg):
                    msg = f"{code}: {msg}"
            else:
                msg = str(errors)
            return f"[{failed_ref}] HTTP {exc.status_code}: {msg}"
        return f"[{failed_ref}] HTTP {exc.status_code}: {body}"
    return f"[{failed_ref}] {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Manifest listing (shared by main.py and webhooks package)
# ---------------------------------------------------------------------------

_MANIFEST_RE = re.compile(r"^\d{8}T\d{6}_[0-9a-f]{8}\.json$")


def list_manifest_ids(runs_dir: str | Path) -> list[str]:
    """Return run IDs from manifest files, newest first."""
    d = Path(runs_dir)
    if not d.exists():
        return []
    return [p.stem for p in sorted(d.glob("*.json"), reverse=True) if _MANIFEST_RE.match(p.name)]
