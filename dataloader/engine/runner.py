"""DAG execution loop, error strategies, staged payload persistence."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Awaitable, Callable

from loguru import logger

from jsonutil import dumps_pretty
from models import (
    DataLoaderConfig,
    HandlerResult,
    ManifestEntry,
    RunManifest,
    _BaseResourceConfig,
)
from models.shared import ErrorStrategy

from .dag import build_dag, inject_legal_entity_psp_connection_id
from .execution_summary import ExecutionResultSummary
from .persist_port import RunStatePersistPort
from .refs import RefRegistry, resolve_refs
from .resource_display import extract_display_name as _extract_display_name
from .run_meta import _now_iso, config_hash


class ExecutionPhaseError(Exception):
    """Failure during DAG execution; carries ``typed_ref`` through ``TaskGroup`` / ``ExceptionGroup``.

    Raised as ``raise ExecutionPhaseError(typed_ref) from exc`` so the original
    API error remains on ``__cause__`` for formatting (e.g. ``APIStatusError``).
    """

    __slots__ = ("typed_ref",)

    def __init__(self, typed_ref: str) -> None:
        self.typed_ref = typed_ref
        super().__init__(f"execution failed for ref {typed_ref!r}")


HandlerFn = Callable[..., Awaitable[HandlerResult]]
EmitFn = Callable[[str, str, dict[str, Any]], Awaitable[None]]
DisconnectCheckFn = Callable[[], bool]
ResourceCreatedFn = Callable[[str, str, str], None | Awaitable[None]]
RunOrgRegisteredFn = Callable[[str, str], None | Awaitable[None]]


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
    on_run_org_registered: RunOrgRegisteredFn | None = None,
    persist: RunStatePersistPort | None = None,
) -> ExecutionResultSummary:
    """Execute the DAG with intra-batch concurrency.

    Baseline refs (in the registry but not in the config) are auto-drained
    from each batch.  Uses ``asyncio.TaskGroup`` for proper cancellation
    of sibling tasks on failure.

    Resources in *update_refs* are routed through *update_dispatch* with
    the existing resource ID so they call ``.update()`` instead of
    ``.create()``.

    *on_run_org_registered* — optional hook (e.g. webhook listener org map);
    called with ``(run_id, mt_org_id)`` when *mt_org_id* is set.

    *persist* — when set, incremental writes go to SQLite (no disk manifest).
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
    if mt_org_id and on_run_org_registered:
        _maybe = on_run_org_registered(run_id, mt_org_id)
        if inspect.isawaitable(_maybe):
            await _maybe
    batch_index = 0

    def _summary() -> ExecutionResultSummary:
        return ExecutionResultSummary(
            run_id=manifest.run_id,
            status=str(manifest.status),
            completed_at=manifest.completed_at,
            resources_created_count=len(manifest.resources_created),
            resources_staged_count=len(manifest.resources_staged),
            resources_failed_count=len(manifest.resources_failed),
        )

    async def _persist_finalize(status: str) -> None:
        manifest.finalize(status)
        if persist is not None:
            await persist.finalize(
                run_id,
                str(manifest.status),
                manifest.completed_at,
                resources_created_count=len(manifest.resources_created),
                resources_staged_count=len(manifest.resources_staged),
                resources_failed_count=len(manifest.resources_failed),
            )

    try:
        while ts.is_active():
            if is_disconnected():
                await _persist_finalize("disconnected")
                return _summary()

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
                            manifest.record_staged(typed_ref, resource.resource_type)
                            st = manifest.resources_staged[-1]
                            if persist is not None:
                                await persist.append_staged_item(
                                    run_id,
                                    typed_ref,
                                    resource.resource_type,
                                    st.staged_at,
                                    dumps_pretty(resolved),
                                )
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
                    _r = on_resource_created(run_id, result.created_id, typed_ref)
                    if inspect.isawaitable(_r):
                        await _r
                    for child_key, child_id in result.child_refs.items():
                        _c = on_resource_created(run_id, child_id, f"{typed_ref}.{child_key}")
                        if inspect.isawaitable(_c):
                            await _c

                entry = ManifestEntry(
                    batch=_batch,
                    resource_type=result.resource_type,
                    typed_ref=typed_ref,
                    created_id=result.created_id,
                    created_at=_now_iso(),
                    deletable=result.deletable,
                    child_refs=result.child_refs,
                )
                manifest.record(entry)
                if persist is not None:
                    await persist.append_created(run_id, entry)
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
                    fe = _failure_entry_extras(leaf)
                    manifest.record_failure(
                        failed_ref,
                        error_detail,
                        error_type=fe.get("error_type"),
                        http_status=fe.get("http_status"),
                        error_cause=fe.get("error_cause"),
                    )
                    if persist is not None and manifest.resources_failed:
                        fl = manifest.resources_failed[-1]
                        await persist.append_failure(
                            run_id,
                            fl.typed_ref,
                            fl.error,
                            failed_at=fl.failed_at,
                            error_type=fl.error_type,
                            http_status=fl.http_status,
                            error_cause=fl.error_cause,
                        )
                    await emit_sse(
                        "error",
                        failed_ref,
                        {"error": error_detail, **{k: v for k, v in fe.items() if v is not None}},
                    )
                await _persist_finalize("failed")
                raise eg.exceptions[0] from None

            ts.done(*to_create)
            batch_index += 1

    except asyncio.CancelledError:
        await _persist_finalize("disconnected")
        return _summary()

    await _persist_finalize("completed")

    return _summary()


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


def _format_exception_detail(
    exc: BaseException,
    failed_ref: str,
    *,
    prefix_ref: bool = True,
) -> str:
    """Extract a human-readable error string, enriching APIStatusError with body details."""
    lead = f"[{failed_ref}] " if prefix_ref and failed_ref else ""
    try:
        from modern_treasury._exceptions import APIStatusError
    except ImportError:
        return f"{lead}{type(exc).__name__}: {exc}"

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
            return f"{lead}HTTP {exc.status_code}: {msg}"
        return f"{lead}HTTP {exc.status_code}: {body}"
    return f"{lead}{type(exc).__name__}: {exc}"


def _failure_entry_extras(exc: BaseException) -> dict[str, str | int | None]:
    """Structured fields for manifests, SSE, and UI (optional)."""
    out: dict[str, str | int | None] = {"error_type": type(exc).__name__}
    try:
        from modern_treasury._exceptions import APIStatusError

        if isinstance(exc, APIStatusError):
            out["http_status"] = exc.status_code
    except ImportError:
        pass
    cause = exc.__cause__
    if cause is not None:
        out["error_cause"] = _format_exception_detail(cause, "", prefix_ref=False)
    return out
