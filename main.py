"""FastAPI application layer for the Modern Treasury Dataloader.

Orchestrates the full workflow: validate → preview → execute → cleanup.
Wires together models, engine, handlers, and baseline modules. Streams
real-time execution progress to an HTMX frontend via Server-Sent Events.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import sys
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from graphlib import CycleError
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger
from modern_treasury import (
    APIConnectionError,
    APITimeoutError,
    AsyncModernTreasury,
    AuthenticationError,
)
from pydantic import ValidationError
from sse_starlette import EventSourceResponse, ServerSentEvent

from baseline import (
    DiscoveryResult,
    PreflightResult,
    baseline_from_discovery,
    discover_org,
    load_baseline,
    run_preflight,
    seed_registry,
)
from engine import (
    RefRegistry,
    RunManifest,
    all_resources,
    build_dag,
    config_hash,
    dry_run,
    execute,
    extract_ref_dependencies,
    generate_run_id,
    typed_ref_for,
)
from handlers import DELETABILITY, build_handler_dispatch
from models import AppSettings, DataLoaderConfig, DisplayPhase
from webhooks import router as webhook_router, index_resource

# ---------------------------------------------------------------------------
# Template engine (module-level — Jinja2 needs no async setup)
# ---------------------------------------------------------------------------

templates = Jinja2Templates(directory="templates")

MT_DOCS: dict[str, str] = {
    "connection": "https://docs.moderntreasury.com/platform/reference/connection-object",
    "legal_entity": "https://docs.moderntreasury.com/platform/reference/legal-entity-object",
    "ledger": "https://docs.moderntreasury.com/platform/reference/ledger-object",
    "counterparty": "https://docs.moderntreasury.com/platform/reference/counterparty-object",
    "ledger_account": "https://docs.moderntreasury.com/platform/reference/ledger-account-object",
    "internal_account": "https://docs.moderntreasury.com/platform/reference/internal-account-object",
    "external_account": "https://docs.moderntreasury.com/platform/reference/external-account-object",
    "ledger_account_category": "https://docs.moderntreasury.com/platform/reference/ledger-account-category-object",
    "virtual_account": "https://docs.moderntreasury.com/platform/reference/virtual-account-object",
    "expected_payment": "https://docs.moderntreasury.com/platform/reference/expected-payment-object",
    "payment_order": "https://docs.moderntreasury.com/platform/reference/payment-order-object",
    "incoming_payment_detail": "https://docs.moderntreasury.com/platform/reference/incoming-payment-detail-object",
    "ledger_transaction": "https://docs.moderntreasury.com/platform/reference/ledger-transaction-object",
    "return": "https://docs.moderntreasury.com/platform/reference/return-object",
    "reversal": "https://docs.moderntreasury.com/platform/reference/reversal-object",
    "category_membership": "https://docs.moderntreasury.com/platform/reference/add-ledger-account-to-category",
    "nested_category": "https://docs.moderntreasury.com/platform/reference/add-sub-category-to-category",
    "sandbox": "https://docs.moderntreasury.com/payments/docs/building-in-sandbox",
    "test_counterparties": "https://docs.moderntreasury.com/payments/docs/test-counterparties",
    "api_keys": "https://docs.moderntreasury.com/platform/reference/api-keys",
}
templates.env.globals["mt_docs"] = MT_DOCS

_css_path = Path("static/style.css")


def _css_version() -> str:
    try:
        return str(int(_css_path.stat().st_mtime))
    except OSError:
        return "1"


templates.env.globals["css_version"] = _css_version()

# ---------------------------------------------------------------------------
# Server-side session cache
# ---------------------------------------------------------------------------

SESSION_TTL_SECONDS = 600


@dataclass
class _SessionState:
    """Cached state between validate and execute."""

    session_token: str
    api_key: str
    org_id: str
    config: DataLoaderConfig
    config_json_text: str
    registry: RefRegistry
    baseline_refs: set[str]
    preflight: PreflightResult
    batches: list[list[str]]
    preview_items: list[dict] = field(default_factory=list)
    discovery: DiscoveryResult | None = None
    created_at: float = field(default_factory=time.time)


_sessions: dict[str, _SessionState] = {}


def _prune_expired_sessions() -> int:
    now = time.time()
    expired = [
        k for k, v in _sessions.items() if now - v.created_at > SESSION_TTL_SECONDS
    ]
    for k in expired:
        del _sessions[k]
    return len(expired)


# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------


def _configure_logging(settings: AppSettings) -> None:
    logger.remove()

    logger.add(
        sys.stderr,
        level=settings.log_level,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level:<8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> - "
            "<level>{message}</level>"
        ),
        colorize=True,
    )

    logger.add(
        "logs/dataloader_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        serialize=True,
        rotation="10 MB",
        retention="7 days",
    )


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = AppSettings()
    app.state.settings = settings
    app.state.baseline_path = settings.baseline_path
    _configure_logging(settings)

    logger.bind(baseline_path=settings.baseline_path).info("Dataloader started")
    yield
    logger.info("Dataloader shutting down")


app = FastAPI(title="MT Dataloader", lifespan=lifespan)

# Mount static files directory (created by frontend step)
static_dir = Path("static")
if static_dir.exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(webhook_router)
app.state.templates = templates

# ---------------------------------------------------------------------------
# SSE helpers
# ---------------------------------------------------------------------------


def _make_emit_sse(
    queue: asyncio.Queue[ServerSentEvent | None],
) -> Any:
    """Return an EmitFn that renders context and enqueues ServerSentEvents.

    The engine calls ``await emit_sse(event_type, typed_ref, data_dict)``.
    """

    async def emit(event_type: str, typed_ref: str, data: dict[str, Any]) -> None:
        context = {"ref": typed_ref, "status": event_type, **data}
        html = templates.get_template("partials/resource_row.html").render(context)
        await queue.put(ServerSentEvent(data=html, event=event_type))

    return emit


def _format_validation_errors(exc: ValidationError) -> list[dict]:
    """Transform Pydantic ValidationError into LLM-readable structured list.

    Each entry has ``path`` (dotted with array indices), ``type``
    (Pydantic error type code), and ``message`` (human-readable).
    """
    errors = []
    for err in exc.errors():
        path = _format_loc(err["loc"])
        errors.append({
            "path": path,
            "type": err["type"],
            "message": err["msg"],
        })
    return errors


def _format_loc(loc: tuple) -> str:
    """Join Pydantic loc tuple into a dotted path with array indices."""
    parts: list[str] = []
    for item in loc:
        if isinstance(item, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{item}]"
            else:
                parts.append(str(item))
        else:
            parts.append(str(item))
    return ".".join(parts)


def _error_html(title: str, detail: str) -> str:
    """Render an error alert partial."""
    return templates.get_template("partials/error_alert.html").render(
        title=title, detail=detail
    )


def _error_response(title: str, detail: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(content=_error_html(title, detail), status_code=status_code)


# ---------------------------------------------------------------------------
# Preview builder
# ---------------------------------------------------------------------------


def _build_preview(
    batches: list[list[str]],
    resource_map: dict[str, Any],
) -> list[dict]:
    """Transform DAG batches into template-friendly preview data.

    ``batches`` from ``dry_run()`` already exclude baseline refs — every
    ref here is a config resource with an entry in ``resource_map``.
    """
    items: list[dict] = []
    for batch_idx, batch in enumerate(batches):
        for ref in batch:
            resource = resource_map[ref]
            meta = getattr(resource, "metadata", {})
            sandbox_info = _extract_sandbox_info(resource)
            items.append(
                {
                    "typed_ref": ref,
                    "resource_type": resource.resource_type,
                    "display_phase": resource.display_phase,
                    "batch": batch_idx,
                    "deletable": DELETABILITY.get(resource.resource_type, False),
                    "has_metadata": bool(meta),
                    "metadata": meta,
                    "deps": list(extract_ref_dependencies(resource))
                    + [d[5:] for d in getattr(resource, "depends_on", []) if d.startswith("$ref:")],
                    "sandbox_info": sandbox_info,
                }
            )
    return items


def _extract_sandbox_info(resource: Any) -> str | None:
    """Return a human-readable sandbox behavior label for counterparties."""
    accounts = getattr(resource, "accounts", None)
    if not accounts:
        return None
    for acct in accounts:
        behavior = getattr(acct, "sandbox_behavior", None)
        if behavior == "success":
            return "sandbox: success"
        elif behavior == "return":
            code = getattr(acct, "sandbox_return_code", None) or "R01"
            return f"sandbox: auto-return {code.upper()}"
        elif behavior == "failure":
            return "sandbox: auto-fail"
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/api/schema")
async def get_schema():
    """Export the full DataLoaderConfig JSON Schema.

    Returns the Pydantic-generated schema (~31KB) with all type
    definitions, enums, required fields, and descriptions.  External
    LLM workflows can use this as a generation target.
    """
    return DataLoaderConfig.model_json_schema()


@app.post("/api/validate-json")
async def validate_json(request: Request):
    """Programmatic JSON validation endpoint for LLM repair loops.

    Accepts raw JSON body, validates it against DataLoaderConfig, and
    returns structured errors suitable for feeding back to an LLM.
    """
    try:
        body = await request.body()
        config = DataLoaderConfig.model_validate_json(body)
    except ValidationError as e:
        return {"valid": False, "errors": _format_validation_errors(e)}

    try:
        batches = dry_run(config)
    except CycleError as e:
        return {"valid": False, "errors": [
            {"path": "(dag)", "type": "cycle_error", "message": str(e)}
        ]}
    except KeyError as e:
        return {"valid": False, "errors": [
            {"path": "(dag)", "type": "unresolvable_ref", "message": str(e)}
        ]}

    return {
        "valid": True,
        "resource_count": sum(len(b) for b in batches),
        "batch_count": len(batches),
        "errors": [],
    }


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/setup")


@app.get("/setup", include_in_schema=False)
async def setup_page(request: Request):
    return templates.TemplateResponse(request, "setup.html", {"title": "Setup"})


@app.post("/api/validate")
async def validate(
    request: Request,
    api_key: str = Form(...),
    org_id: str = Form(...),
    config_file: UploadFile | None = File(None),
    config_json: str | None = Form(None),
):
    """Validate API key, run preflight, parse config, compute DAG, cache state."""
    _prune_expired_sessions()

    # 1. Parse JSON config — accept file upload or raw JSON text
    if config_json and config_json.strip():
        raw_json = config_json.strip().encode()
    elif config_file and config_file.size:
        raw_json = await config_file.read()
    else:
        return _error_response("Missing Config", "Upload a JSON file or paste JSON directly.")

    try:
        config = DataLoaderConfig.model_validate_json(raw_json)
    except ValidationError as e:
        structured = _format_validation_errors(e)
        detail_lines = [f"• {err['path']}: {err['message']}" for err in structured]
        return _error_response(
            "Config Validation Error",
            "\n".join(detail_lines) or str(e),
        )

    # 2. Ping MT to validate API key
    async with AsyncModernTreasury(
        api_key=api_key, organization_id=org_id
    ) as client:
        try:
            await client.ping()
        except AuthenticationError:
            return _error_response(
                "Authentication Error", "Invalid API key or org ID"
            )

        # 3. Discover org (replaces static baseline load)
        discovery: DiscoveryResult | None = None
        try:
            discovery = await discover_org(client)
            baseline = baseline_from_discovery(discovery)
        except (APIConnectionError, APITimeoutError) as exc:
            logger.warning(
                "Discovery failed, falling back to static baseline: {}",
                str(exc),
            )
            baseline = load_baseline(request.app.state.baseline_path)

        # 4. Seed registry
        registry = RefRegistry()
        baseline_refs = seed_registry(baseline, registry)

        # 5. Preflight: explicit branch
        if discovery is not None:
            preflight = PreflightResult()
        else:
            preflight = await run_preflight(client, baseline)

    if not preflight.passed:
        return templates.TemplateResponse(
            request,
            "partials/preflight_failure.html",
            {"preflight": preflight},
        )

    # 4. Dry-run: DAG construction + ref resolution
    try:
        batches = dry_run(config, baseline_refs)
    except CycleError as e:
        return _error_response("Cycle Error", f"Circular dependency: {e}")
    except KeyError as e:
        return _error_response("Reference Error", str(e))

    # 5. Build preview data
    resource_map = {typed_ref_for(r): r for r in all_resources(config)}
    preview_items = _build_preview(batches, resource_map)

    # 6. Pretty-print the config JSON for the editor
    config_json_text = json.dumps(
        json.loads(raw_json), indent=2, ensure_ascii=False
    )

    # 7. Cache session state
    token = secrets.token_urlsafe(32)
    _sessions[token] = _SessionState(
        session_token=token,
        api_key=api_key,
        org_id=org_id,
        config=config,
        config_json_text=config_json_text,
        registry=registry,
        baseline_refs=baseline_refs,
        preflight=preflight,
        batches=batches,
        preview_items=preview_items,
        discovery=discovery,
    )

    return templates.TemplateResponse(
        request,
        "preview.html",
        {
            "session_token": token,
            "batches": batches,
            "preview_items": preview_items,
            "preflight": preflight,
            "config_hash": config_hash(config),
            "resource_count": sum(len(b) for b in batches),
            "deletable_count": sum(
                1
                for item in preview_items
                if item["deletable"]
            ),
            "non_deletable_count": sum(
                1
                for item in preview_items
                if not item["deletable"]
            ),
            "display_phases": DisplayPhase,
            "discovery": discovery,
            "config_json_text": config_json_text,
        },
    )


@app.post("/api/revalidate")
async def revalidate(
    request: Request,
    session_token: str = Form(...),
    config_json: str = Form(...),
):
    """Re-validate edited JSON using credentials from an existing session."""
    session = _sessions.get(session_token)
    if not session:
        return _error_response("Session Expired", "Please start over from Setup.")

    raw_json = config_json.strip().encode()
    try:
        config = DataLoaderConfig.model_validate_json(raw_json)
    except ValidationError as e:
        structured = _format_validation_errors(e)
        detail_lines = [f"• {err['path']}: {err['message']}" for err in structured]
        return _error_response(
            "Config Validation Error",
            "\n".join(detail_lines) or str(e),
        )

    async with AsyncModernTreasury(
        api_key=session.api_key, organization_id=session.org_id
    ) as client:
        discovery: DiscoveryResult | None = None
        try:
            discovery = await discover_org(client)
            baseline = baseline_from_discovery(discovery)
        except (APIConnectionError, APITimeoutError) as exc:
            logger.warning("Discovery failed during revalidate: {}", str(exc))
            baseline = load_baseline(request.app.state.baseline_path)

        registry = RefRegistry()
        baseline_refs = seed_registry(baseline, registry)

        if discovery is not None:
            preflight = PreflightResult()
        else:
            preflight = await run_preflight(client, baseline)

    if not preflight.passed:
        return templates.TemplateResponse(
            request,
            "partials/preflight_failure.html",
            {"preflight": preflight},
        )

    try:
        batches = dry_run(config, baseline_refs)
    except CycleError as e:
        return _error_response("Cycle Error", f"Circular dependency: {e}")
    except KeyError as e:
        return _error_response("Reference Error", str(e))

    resource_map = {typed_ref_for(r): r for r in all_resources(config)}
    preview_items = _build_preview(batches, resource_map)

    config_json_text = json.dumps(
        json.loads(raw_json), indent=2, ensure_ascii=False
    )

    new_token = secrets.token_urlsafe(32)
    _sessions[new_token] = _SessionState(
        session_token=new_token,
        api_key=session.api_key,
        org_id=session.org_id,
        config=config,
        config_json_text=config_json_text,
        registry=registry,
        baseline_refs=baseline_refs,
        preflight=preflight,
        batches=batches,
        preview_items=preview_items,
        discovery=discovery,
    )

    del _sessions[session_token]

    return templates.TemplateResponse(
        request,
        "preview.html",
        {
            "session_token": new_token,
            "batches": batches,
            "preview_items": preview_items,
            "preflight": preflight,
            "config_hash": config_hash(config),
            "resource_count": sum(len(b) for b in batches),
            "deletable_count": sum(
                1 for item in preview_items if item["deletable"]
            ),
            "non_deletable_count": sum(
                1 for item in preview_items if not item["deletable"]
            ),
            "display_phases": DisplayPhase,
            "discovery": discovery,
            "config_json_text": config_json_text,
        },
    )


@app.post("/api/execute")
async def execute_page(
    request: Request,
    session_token: str = Form(...),
):
    """Return execute page with pre-rendered rows and SSE container.

    Does NOT pop the session — the stream endpoint does that.
    """
    session = _sessions.get(session_token)
    if not session:
        return _error_response("Session Expired", "Please re-validate your config.")

    return templates.TemplateResponse(
        request,
        "execute.html",
        {
            "session_token": session_token,
            "preview_items": session.preview_items,
            "batches": session.batches,
            "resource_count": sum(len(b) for b in session.batches),
            "display_phases": DisplayPhase,
        },
    )


@app.get("/api/execute/stream")
async def execute_stream(
    request: Request,
    session_token: str,
):
    """SSE stream endpoint. Pops session and runs the DAG engine."""
    session = _sessions.pop(session_token, None)
    if not session:
        async def _error_gen():
            html = _error_html("Session Expired", "Please re-validate your config.")
            yield ServerSentEvent(data=html, event="error")
            yield ServerSentEvent(data="", event="close")
        return EventSourceResponse(_error_gen())

    async def event_generator():
        queue: asyncio.Queue[ServerSentEvent | None] = asyncio.Queue()
        emit_sse = _make_emit_sse(queue)
        run_id = generate_run_id()
        settings = request.app.state.settings
        semaphore = asyncio.Semaphore(settings.max_concurrent_requests)

        config_path = Path(settings.runs_dir) / f"{run_id}_config.json"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(session.config_json_text, encoding="utf-8")

        disconnected = False

        async def run_engine():
            nonlocal disconnected
            async with AsyncModernTreasury(
                api_key=session.api_key,
                organization_id=session.org_id,
            ) as client:
                handler_dispatch = build_handler_dispatch(client, emit_sse)
                try:
                    manifest = await execute(
                        config=session.config,
                        registry=session.registry,
                        handler_dispatch=handler_dispatch,
                        run_id=run_id,
                        semaphore=semaphore,
                        emit_sse=emit_sse,
                        is_disconnected=lambda: disconnected,
                        runs_dir=settings.runs_dir,
                        on_resource_created=index_resource,
                    )
                    html = templates.get_template(
                        "partials/run_complete.html"
                    ).render(manifest=manifest, run_id=run_id)
                    await queue.put(
                        ServerSentEvent(data=html, event="run_complete")
                    )
                except Exception as exc:
                    logger.bind(run_id=run_id, error=str(exc)).error(
                        "Execution failed"
                    )
                    html = templates.get_template("partials/error.html").render(
                        error=str(exc)
                    )
                    await queue.put(ServerSentEvent(data=html, event="error"))
                finally:
                    await queue.put(ServerSentEvent(data="", event="close"))
                    await queue.put(None)

        task = asyncio.create_task(run_engine())

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield event
        except asyncio.CancelledError:
            disconnected = True
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    return EventSourceResponse(event_generator(), ping=15)


@app.get("/runs", include_in_schema=False)
async def runs_page(request: Request):
    return templates.TemplateResponse(request, "runs_page.html", {"title": "Runs"})


_MANIFEST_RE = re.compile(r"^\d{8}T\d{6}_[0-9a-f]{8}\.json$")


@app.get("/api/runs")
async def list_runs(request: Request):
    """List past run manifests."""
    runs_dir = Path(request.app.state.settings.runs_dir)
    manifests: list[RunManifest] = []
    if runs_dir.exists():
        for path in sorted(runs_dir.glob("*.json"), reverse=True):
            if not _MANIFEST_RE.match(path.name):
                continue
            try:
                manifests.append(RunManifest.load(path))
            except Exception as e:
                logger.bind(path=str(path), error=str(e)).warning(
                    "Failed to load manifest"
                )

    return templates.TemplateResponse(
        request,
        "runs.html",
        {"manifests": manifests, "deletability": DELETABILITY},
    )


@app.post("/api/cleanup/{run_id}")
async def cleanup_page(
    request: Request,
    run_id: str,
    api_key: str = Form(...),
    org_id: str = Form(...),
):
    """Return cleanup page with pre-rendered rows and SSE container."""
    manifest_path = (
        Path(request.app.state.settings.runs_dir) / f"{run_id}.json"
    )
    if not manifest_path.exists():
        return _error_response("Not Found", f"Run '{run_id}' not found", 404)

    manifest = RunManifest.load(manifest_path)
    token = f"cleanup-{secrets.token_urlsafe(16)}"
    _sessions[token] = _SessionState(
        session_token=token,
        api_key=api_key,
        org_id=org_id,
        config=DataLoaderConfig(),
        registry=RefRegistry(),
        baseline_refs=set(),
        preflight=PreflightResult(),
        batches=[],
    )
    _sessions[token]._cleanup_manifest = manifest  # type: ignore[attr-defined]

    reversed_resources = list(reversed(manifest.resources_created))
    return templates.TemplateResponse(
        request,
        "cleanup.html",
        {
            "cleanup_token": token,
            "run_id": run_id,
            "resources": reversed_resources,
            "deletability": DELETABILITY,
        },
    )


@app.get("/api/cleanup/stream/{token}")
async def cleanup_stream(token: str):
    """SSE stream for cleanup progress."""
    session = _sessions.pop(token, None)
    if not session:
        async def _error_gen():
            html = _error_html("Session Expired", "Cleanup session not found.")
            yield ServerSentEvent(data=html, event="error")
            yield ServerSentEvent(data="", event="close")
        return EventSourceResponse(_error_gen())

    manifest: RunManifest = session._cleanup_manifest  # type: ignore[attr-defined]

    async def cleanup_generator():
        async with AsyncModernTreasury(
            api_key=session.api_key, organization_id=session.org_id
        ) as client:
            reversed_resources = list(reversed(manifest.resources_created))

            try:
                for entry in reversed_resources:
                    action, status = await _cleanup_one(client, entry)
                    html = templates.get_template(
                        "partials/cleanup_row.html"
                    ).render(entry=entry, action=action, status=status)
                    yield ServerSentEvent(
                        data=html, event="cleanup_progress"
                    )

                html = templates.get_template(
                    "partials/cleanup_complete.html"
                ).render(run_id=manifest.run_id)
                yield ServerSentEvent(
                    data=html, event="cleanup_complete"
                )
            except asyncio.CancelledError:
                pass
            finally:
                yield ServerSentEvent(data="", event="close")

    return EventSourceResponse(cleanup_generator(), ping=15)


# ---------------------------------------------------------------------------
# Cleanup helpers
# ---------------------------------------------------------------------------


async def _cleanup_one(
    client: AsyncModernTreasury,
    entry: Any,
) -> tuple[str, str]:
    """Dispatch a single cleanup action.

    Returns ``(action, status)`` where action is ``deleted``, ``archived``,
    ``removed``, or ``skipped`` and status is ``success`` or an error string.

    Resource-type-specific branches (archive, remove) are checked FIRST,
    before the generic deletable check — ledger_transaction has
    ``deletable=False`` in the manifest but should still be archived.
    """
    rtype = entry.resource_type

    try:
        if rtype == "ledger_transaction":
            await client.ledger_transactions.update(
                entry.created_id, status="archived"
            )
            return ("archived", "success")

        elif rtype == "category_membership":
            cat_id, la_id = entry.created_id.split(":", 1)
            await client.ledger_account_categories.remove_ledger_account(
                la_id, id=cat_id
            )
            return ("removed", "success")

        elif rtype == "nested_category":
            parent_id, sub_id = entry.created_id.split(":", 1)
            await client.ledger_account_categories.remove_nested_category(
                sub_id, id=parent_id
            )
            return ("removed", "success")

        elif entry.deletable:
            resource_client = _get_resource_client(client, rtype)
            if resource_client is None:
                return ("skipped", f"no cleanup handler for {rtype}")
            await resource_client.delete(entry.created_id)
            return ("deleted", "success")

        else:
            return ("skipped", "not deletable")

    except Exception as e:
        logger.bind(ref=entry.typed_ref, error=str(e)).warning("Cleanup failed")
        return ("failed", str(e))


def _get_resource_client(
    client: AsyncModernTreasury, resource_type: str
) -> Any | None:
    """Map a resource type string to its SDK sub-client for deletion."""
    return {
        "counterparty": client.counterparties,
        "external_account": client.external_accounts,
        "virtual_account": client.virtual_accounts,
        "ledger": client.ledgers,
        "ledger_account": client.ledger_accounts,
        "ledger_account_category": client.ledger_account_categories,
        "expected_payment": client.expected_payments,
    }.get(resource_type)
