"""Loader validation: pure steps + full async pipeline (no FastAPI).

Headless JSON API (``validate_json``), full setup validate/revalidate
(``run_loader_validation_pipeline`` → ``apply_loader_validation_success_to_session``),
and shared Pydantic parse helpers for ``flows.py``. Plan 04 — Wave D + Phase 2 dedupe.
"""

from __future__ import annotations

import json
import secrets
from collections import Counter
from dataclasses import asdict, dataclass, field
from graphlib import CycleError
from typing import Any, Literal, TypeVar

from loguru import logger
from modern_treasury import (
    APIConnectionError,
    APITimeoutError,
    AsyncModernTreasury,
    AuthenticationError,
)
from pydantic import BaseModel, ValidationError

from dataloader.engine import RefRegistry, all_resources, dry_run, typed_ref_for
from dataloader.helpers import (
    build_discovered_id_lookup,
    build_preview,
    format_validation_errors,
)
from dataloader.observability.loader_validation_trace import (
    Status,
    StatusCode,
    loader_span,
    loader_validation_tracer,
)
from dataloader.session import SessionState
from flow_compiler import AuthoringConfig, ExecutionPlan, compile_to_plan, flatten_actor_refs
from flow_compiler.flow_validator import validate_flow
from flow_compiler.pipeline import (
    Pass,
    pass_compile_to_ir,
    pass_emit_resources,
    pass_expand_instances,
)
from models import DataLoaderConfig
from models.loader_setup_json import (
    LoaderSetupEnvelopeV1,
    LoaderSetupErrorItem,
    LoaderSetupFlowDiagnosticItem,
    LoaderSetupPhase,
    error_items_from_pydantic_validation,
)
from org import (
    DiscoveryResult,
    OrgRegistry,
    discover_org,
    reconcile_config,
    sync_connection_entities_from_reconciliation,
)

# Subset pipeline for ``POST /api/validate-json`` (no diagrams / view_data).
HEADLESS_VALIDATE_JSON_PIPELINE: tuple[Pass, ...] = (
    pass_expand_instances,
    pass_compile_to_ir,
    pass_emit_resources,
)


BodyInvalidKind = Literal["utf8", "json", "not_object"]


@dataclass(frozen=True)
class ParseLoaderOutcome:
    """Result of parsing raw bytes as ``DataLoaderConfig`` JSON (single ``json.loads`` + validate)."""

    config: DataLoaderConfig | None = None
    error: ValidationError | None = None
    body_invalid: BodyInvalidKind | None = None


def invalid_body_kind_message(kind: BodyInvalidKind) -> str:
    if kind == "utf8":
        return "Body must be valid UTF-8."
    if kind == "json":
        return "Body must be valid JSON."
    return "Body must be a JSON object (DataLoaderConfig root)."


def parse_loader_config_bytes(raw_json: bytes) -> ParseLoaderOutcome:
    try:
        text = raw_json.decode("utf-8")
    except UnicodeDecodeError:
        return ParseLoaderOutcome(body_invalid="utf8")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return ParseLoaderOutcome(body_invalid="json")
    if not isinstance(obj, dict):
        return ParseLoaderOutcome(body_invalid="not_object")
    try:
        return ParseLoaderOutcome(config=DataLoaderConfig.model_validate(obj))
    except ValidationError as e:
        return ParseLoaderOutcome(error=e)


def collect_flow_diagnostic_dicts(flow_irs: list, expanded_flows: list) -> list[dict]:
    """Advisory ``validate_flow`` rows (``dataclasses.asdict(FlowDiagnostic)``)."""
    dicts: list[dict] = []
    if len(flow_irs) != len(expanded_flows):
        logger.warning(
            "flow_irs / expanded_flows length mismatch: {} vs {}",
            len(flow_irs),
            len(expanded_flows),
        )
    for ir, fc in zip(flow_irs, expanded_flows):
        for d in validate_flow(ir, actor_refs=flatten_actor_refs(fc.actors)):
            dicts.append(asdict(d))
    if dicts:
        by_rule = Counter(d["rule_id"] for d in dicts)
        logger.debug(
            "Flow advisory diagnostics: {} finding(s) by_rule={}",
            len(dicts),
            dict(by_rule),
        )
    return dicts


def flow_diagnostic_dicts_to_items(dicts: list[dict]) -> list[LoaderSetupFlowDiagnosticItem]:
    return [LoaderSetupFlowDiagnosticItem.model_validate(d) for d in dicts]


def parse_loader_config_json_text(text: str) -> ParseLoaderOutcome:
    """Parse UTF-8 JSON text as ``DataLoaderConfig`` (shared with ``flows.py``)."""
    return parse_loader_config_bytes(text.encode("utf-8"))


TModel = TypeVar("TModel", bound=BaseModel)


def try_parse_pydantic_json_bytes(
    model_cls: type[TModel],
    body: bytes,
) -> tuple[TModel | None, ValidationError | None]:
    """Parse JSON bytes with ``model_validate_json``; return ``(value, None)`` or ``(None, error)``."""
    try:
        return model_cls.model_validate_json(body), None
    except ValidationError as e:
        return None, e


def try_parse_pydantic_obj(
    model_cls: type[TModel],
    data: Any,
) -> tuple[TModel | None, ValidationError | None]:
    """Validate an object with ``model_validate``; return ``(value, None)`` or ``(None, error)``."""
    try:
        return model_cls.model_validate(data), None
    except ValidationError as e:
        return None, e


def require_pydantic_obj(model_cls: type[TModel], data: Any) -> TModel:
    """Like ``try_parse_pydantic_obj`` but re-raise ``ValidationError`` on failure."""
    value, err = try_parse_pydantic_obj(model_cls, data)
    if err is not None:
        raise err
    assert value is not None
    return value


@dataclass(frozen=True)
class LoaderCompileFailure:
    """Compiler failure for both HTML pipeline (string) and JSON API (ErrorItem)."""

    pipeline_message: str
    errors: list[LoaderSetupErrorItem]


def compile_loader_plan(
    authoring: AuthoringConfig,
    *,
    pipeline: tuple | None = None,
) -> ExecutionPlan | LoaderCompileFailure:
    try:
        plan = (
            compile_to_plan(authoring)
            if pipeline is None
            else compile_to_plan(authoring, pipeline=pipeline)
        )
        return plan
    except (ValueError, KeyError, NotImplementedError) as e:
        return LoaderCompileFailure(
            pipeline_message=f"Compiler Error\n{e}",
            errors=[
                LoaderSetupErrorItem(
                    code="compile_error",
                    message=str(e),
                    path="(compiler)",
                )
            ],
        )


def dry_run_value_error_message(exc: ValueError) -> str:
    """Turn dry_run ValueError into a user-facing detail (may be multi-line)."""
    msg = str(exc)
    if "staged resource" in msg.lower():
        msg += (
            "\n\n"
            "Hint: `complete_verification` defaults to staged. Downstream steps "
            "(incoming_payment_detail, payment_order, …) that list it in `depends_on` "
            'cannot sit in the same non-staged batch. Fix: set `"staged": false` on '
            "those `complete_verification` steps if verification is done before this load, "
            'or set `"staged": true` on the downstream payment steps as well.'
        )
    return msg


@dataclass(frozen=True)
class LoaderDryRunSuccess:
    batches: list[list[str]]


@dataclass(frozen=True)
class LoaderDryRunFailure:
    pipeline_message: str
    errors: list[LoaderSetupErrorItem]


def try_loader_dry_run(
    config: DataLoaderConfig,
    known_refs: set[str] | None = None,
    skip_refs: set[str] | None = None,
) -> LoaderDryRunSuccess | LoaderDryRunFailure:
    try:
        batches = dry_run(config, known_refs, skip_refs=skip_refs)
        return LoaderDryRunSuccess(batches=batches)
    except CycleError as e:
        return LoaderDryRunFailure(
            pipeline_message=f"Cycle Error\nCircular dependency: {e}",
            errors=[
                LoaderSetupErrorItem(
                    code="cycle_error",
                    message=str(e),
                    path="(dag)",
                )
            ],
        )
    except KeyError as e:
        return LoaderDryRunFailure(
            pipeline_message=f"Reference Error\n{e}",
            errors=[
                LoaderSetupErrorItem(
                    code="unresolvable_ref",
                    message=str(e),
                    path="(dag)",
                )
            ],
        )
    except ValueError as e:
        detail = dry_run_value_error_message(e)
        return LoaderDryRunFailure(
            pipeline_message=f"Can't build execution plan\n{detail}",
            errors=[
                LoaderSetupErrorItem(
                    code="staged_dependency",
                    message=detail,
                    path="(dag)",
                )
            ],
        )


@dataclass(frozen=True)
class HeadlessValidateJsonOutcome:
    """Outcome for ``POST /api/validate-json`` (after wire-format pre-check for 422)."""

    ok: bool
    phase: LoaderSetupPhase | None
    errors: list[LoaderSetupErrorItem]
    data: dict
    diagnostics: tuple[LoaderSetupFlowDiagnosticItem, ...] = ()


def headless_outcome_to_envelope(outcome: HeadlessValidateJsonOutcome) -> LoaderSetupEnvelopeV1:
    return LoaderSetupEnvelopeV1(
        ok=outcome.ok,
        phase=outcome.phase,
        errors=list(outcome.errors),
        diagnostics=list(outcome.diagnostics),
        data=outcome.data,
    )


def run_headless_validate_after_parse(
    config: DataLoaderConfig, raw_json: bytes
) -> HeadlessValidateJsonOutcome:
    """Headless pipeline when ``DataLoaderConfig`` is already parsed (single wire parse at router)."""
    tracer = loader_validation_tracer()
    with tracer.start_as_current_span("loader_validation.headless.after_parse") as root:
        had_funds_flows = bool(config.funds_flows)
        with loader_span("loader_validation.headless.compile"):
            authoring = AuthoringConfig.from_json(raw_json)
            compiled = compile_loader_plan(authoring, pipeline=HEADLESS_VALIDATE_JSON_PIPELINE)
        if isinstance(compiled, LoaderCompileFailure):
            root.set_attribute("loader.validation.failed_phase", "compile")
            root.set_status(Status(StatusCode.ERROR, "compile"))
            return HeadlessValidateJsonOutcome(
                ok=False,
                phase="compile",
                errors=list(compiled.errors),
                data={},
            )

        plan = compiled
        config = plan.config
        flow_irs = list(plan.flow_irs)
        expanded_flows = list(plan.expanded_flows)
        diag_dicts = collect_flow_diagnostic_dicts(flow_irs, expanded_flows)
        diag_items = tuple(flow_diagnostic_dicts_to_items(diag_dicts))

        with loader_span("loader_validation.headless.dag"):
            dry = try_loader_dry_run(config)
        if isinstance(dry, LoaderDryRunFailure):
            root.set_attribute("loader.validation.failed_phase", "dag")
            root.set_status(Status(StatusCode.ERROR, "dag"))
            return HeadlessValidateJsonOutcome(
                ok=False,
                phase="dag",
                errors=list(dry.errors),
                data={},
                diagnostics=diag_items,
            )

        batches = dry.batches
        root.set_attribute("loader.validation.ok", True)
        root.set_status(Status(StatusCode.OK))
        return HeadlessValidateJsonOutcome(
            ok=True,
            phase="complete",
            errors=[],
            data={
                "resource_count": sum(len(b) for b in batches),
                "batch_count": len(batches),
                "has_funds_flows": had_funds_flows,
            },
            diagnostics=diag_items,
        )


def run_headless_validate_json(raw_json: bytes) -> HeadlessValidateJsonOutcome:
    """Parse → compile (headless pipeline) → ``validate_flow`` diagnostics → ``dry_run``."""
    tracer = loader_validation_tracer()
    with tracer.start_as_current_span("loader_validation.headless.json") as root:
        with loader_span("loader_validation.headless.parse"):
            parsed = parse_loader_config_bytes(raw_json)
        if parsed.body_invalid is not None:
            root.set_attribute("loader.validation.failed_phase", "parse")
            root.set_status(Status(StatusCode.ERROR, "parse"))
            return HeadlessValidateJsonOutcome(
                ok=False,
                phase=None,
                errors=[
                    LoaderSetupErrorItem(
                        code="invalid_body",
                        message=invalid_body_kind_message(parsed.body_invalid),
                        path=None,
                    )
                ],
                data={},
            )
        if parsed.error is not None:
            root.set_attribute("loader.validation.failed_phase", "parse")
            root.set_status(Status(StatusCode.ERROR, "parse"))
            return HeadlessValidateJsonOutcome(
                ok=False,
                phase="parse",
                errors=error_items_from_pydantic_validation(parsed.error),
                data={},
            )
        if parsed.config is None:
            root.set_attribute("loader.validation.failed_phase", "parse")
            root.set_status(Status(StatusCode.ERROR, "parse"))
            return HeadlessValidateJsonOutcome(
                ok=False,
                phase="parse",
                errors=[
                    LoaderSetupErrorItem(
                        code="validation_error",
                        message="Invalid configuration.",
                        path=None,
                    )
                ],
                data={},
            )
        return run_headless_validate_after_parse(parsed.config, raw_json)


# ---------------------------------------------------------------------------
# Full loader pipeline (validate / revalidate) — Wave D validate → apply → persist
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoaderValidationFailure:
    """Failure from ``run_loader_validation_pipeline``.

    ``v1_*`` fields are the canonical failure model (§ v1 JSON + HTMX); ``message`` remains a
    human-oriented summary string for logs and backward compatibility.
    """

    message: str
    v1_phase: LoaderSetupPhase | None = None
    v1_errors: tuple[LoaderSetupErrorItem, ...] = ()
    #: Advisory ``validate_flow`` rows (same dict shape as ``collect_flow_diagnostic_dicts``); empty if compile did not run.
    v1_flow_diagnostic_dicts: tuple[dict[str, Any], ...] = ()


@dataclass
class LoaderValidationSuccess:
    """Successful full pipeline result before applying to ``SessionState``."""

    config: DataLoaderConfig
    config_json_text: str
    authoring_config_json: str
    flow_irs: list
    expanded_flows: list
    mermaid_diagrams: list | None
    view_data_cache: list | None
    discovery: DiscoveryResult | None
    org_registry: OrgRegistry | None
    reconciliation: object | None
    registry: RefRegistry
    skip_refs: set = field(default_factory=set)
    batches: list = field(default_factory=list)
    preview_items: list = field(default_factory=list)
    flow_diagnostics: list = field(default_factory=list)


def edited_resource_typed_refs(
    prior: DataLoaderConfig | None,
    config: DataLoaderConfig,
) -> set[str]:
    """Refs whose serialized resource payload changed (revalidate reconciliation)."""
    if prior is None:
        return set()
    old_map = {typed_ref_for(r): r for r in all_resources(prior)}
    changed: set[str] = set()
    for r in all_resources(config):
        ref = typed_ref_for(r)
        old = old_map.get(ref)
        if old is None:
            continue
        if old.model_dump_json(exclude_none=True) != r.model_dump_json(exclude_none=True):
            changed.add(ref)
    return changed


async def run_loader_validation_pipeline(
    raw_json: bytes,
    api_key: str,
    org_id: str,
    *,
    reconcile_overrides: dict | None = None,
    manual_mappings: dict | None = None,
    prior_config: DataLoaderConfig | None = None,
) -> LoaderValidationSuccess | LoaderValidationFailure:
    """Parse → compile → discover → reconcile → DAG → preview (no session or HTTP)."""
    tracer = loader_validation_tracer()
    with tracer.start_as_current_span(
        "loader_validation.pipeline",
        attributes={"loader.org.id": org_id},
    ) as root:
        with loader_span("loader_validation.parse"):
            parsed = parse_loader_config_bytes(raw_json)
        if parsed.body_invalid is not None:
            msg = invalid_body_kind_message(parsed.body_invalid)
            root.set_attribute("loader.validation.failed_phase", "parse")
            root.set_status(Status(StatusCode.ERROR, "parse"))
            return LoaderValidationFailure(
                message=f"Config Validation Error\n{msg}",
                v1_phase="parse",
                v1_errors=(LoaderSetupErrorItem(code="invalid_body", message=msg, path=None),),
            )
        if parsed.error is not None:
            structured = format_validation_errors(parsed.error)
            detail_lines = [f"• {err['path']}: {err['message']}" for err in structured]
            items = tuple(error_items_from_pydantic_validation(parsed.error))
            root.set_attribute("loader.validation.failed_phase", "parse")
            root.set_status(Status(StatusCode.ERROR, "parse"))
            return LoaderValidationFailure(
                message="Config Validation Error\n"
                + ("\n".join(detail_lines) or str(parsed.error)),
                v1_phase="parse",
                v1_errors=items,
            )

        if parsed.config is None:
            root.set_attribute("loader.validation.failed_phase", "parse")
            root.set_status(Status(StatusCode.ERROR, "parse"))
            return LoaderValidationFailure(
                message="Config Validation Error\nInvalid configuration.",
                v1_phase="parse",
                v1_errors=(
                    LoaderSetupErrorItem(
                        code="validation_error",
                        message="Invalid configuration.",
                        path=None,
                    ),
                ),
            )
        config = parsed.config
        authoring_config_json = config.model_dump_json(indent=2, exclude_none=True)

        with loader_span("loader_validation.compile"):
            authoring = AuthoringConfig.from_json(raw_json)
            compiled = compile_loader_plan(authoring)
        if isinstance(compiled, LoaderCompileFailure):
            root.set_attribute("loader.validation.failed_phase", "compile")
            root.set_status(Status(StatusCode.ERROR, "compile"))
            return LoaderValidationFailure(
                message=compiled.pipeline_message,
                v1_phase="compile",
                v1_errors=tuple(compiled.errors),
            )

        plan = compiled
        config = plan.config
        flow_irs = list(plan.flow_irs)
        expanded_flows = list(plan.expanded_flows)
        mermaid_diagrams = list(plan.mermaid_diagrams) if plan.mermaid_diagrams else None
        view_data_cache = list(plan.view_data) if plan.view_data else None

        flow_diag_dicts = collect_flow_diagnostic_dicts(flow_irs, expanded_flows)

        discovery: DiscoveryResult | None = None
        org_registry: OrgRegistry | None = None
        with loader_span("loader_validation.discover"):
            async with AsyncModernTreasury(api_key=api_key, organization_id=org_id) as client:
                try:
                    await client.ping()
                except AuthenticationError:
                    root.set_attribute("loader.validation.failed_phase", "discover")
                    root.set_status(Status(StatusCode.ERROR, "discover"))
                    return LoaderValidationFailure(
                        message="Authentication Error\nInvalid API key or org ID",
                        v1_phase="discover",
                        v1_errors=(
                            LoaderSetupErrorItem(
                                code="auth_error",
                                message="Invalid API key or org ID",
                                path=None,
                            ),
                        ),
                        v1_flow_diagnostic_dicts=tuple(flow_diag_dicts),
                    )
                try:
                    discovery = await discover_org(client, config=config)
                    org_registry = OrgRegistry.from_discovery(discovery)
                except (APIConnectionError, APITimeoutError) as exc:
                    logger.warning("Discovery failed: {}", str(exc))

        with loader_span("loader_validation.reconcile"):
            registry = RefRegistry()
            known_refs: set[str] = set()
            if org_registry is not None:
                known_refs = org_registry.seed_engine_registry(registry)

            reconciliation = None
            skip_refs: set[str] = set()
            if discovery is not None:
                reconciliation = reconcile_config(config, discovery)

                registered_refs: set[str] = set()
                overrides = reconcile_overrides or {}
                mappings = manual_mappings or {}
                force_new_refs = edited_resource_typed_refs(prior_config, config)

                for m in reconciliation.matches:
                    if m.config_ref in overrides:
                        val = overrides[m.config_ref]
                        if isinstance(val, dict):
                            m.use_existing = val.get("use_existing", True)
                            if "discovered_id" in val:
                                m.discovered_id = val["discovered_id"]
                        else:
                            m.use_existing = bool(val)
                    if m.config_ref in force_new_refs:
                        m.use_existing = False
                    if m.use_existing and m.config_ref not in registered_refs:
                        registry.register_or_update(m.config_ref, m.discovered_id)
                        skip_refs.add(m.config_ref)
                        registered_refs.add(m.config_ref)
                        for ck, cid in m.child_refs.items():
                            registry.register_or_update(f"{m.config_ref}.{ck}", cid)

                if mappings:
                    disc_by_id = build_discovered_id_lookup(discovery)
                    for config_ref, disc_id in mappings.items():
                        if not disc_id or config_ref in registered_refs:
                            continue
                        if disc_by_id.get(disc_id):
                            registry.register_or_update(config_ref, disc_id)
                            skip_refs.add(config_ref)
                            registered_refs.add(config_ref)
                            if config_ref in reconciliation.unmatched_config:
                                reconciliation.unmatched_config.remove(config_ref)

                sync_connection_entities_from_reconciliation(
                    config,
                    discovery,
                    reconciliation,
                    mappings,
                )

        with loader_span("loader_validation.dag"):
            dry = try_loader_dry_run(config, known_refs, skip_refs=skip_refs)
        if isinstance(dry, LoaderDryRunFailure):
            root.set_attribute("loader.validation.failed_phase", "dag")
            root.set_status(Status(StatusCode.ERROR, "dag"))
            return LoaderValidationFailure(
                message=dry.pipeline_message,
                v1_phase="dag",
                v1_errors=tuple(dry.errors),
                v1_flow_diagnostic_dicts=tuple(flow_diag_dicts),
            )
        batches = dry.batches

        with loader_span("loader_validation.preview"):
            resource_map = {typed_ref_for(r): r for r in all_resources(config)}
            preview_items = build_preview(
                batches,
                resource_map,
                skip_refs=skip_refs,
                reconciliation=reconciliation,
            )

            config_json_text = config.model_dump_json(indent=2, exclude_none=True)

        root.set_attribute("loader.validation.ok", True)
        root.set_status(Status(StatusCode.OK))
        return LoaderValidationSuccess(
            config=config,
            config_json_text=config_json_text,
            authoring_config_json=authoring_config_json,
            flow_irs=flow_irs,
            expanded_flows=expanded_flows,
            mermaid_diagrams=mermaid_diagrams,
            view_data_cache=view_data_cache,
            discovery=discovery,
            org_registry=org_registry,
            reconciliation=reconciliation,
            registry=registry,
            skip_refs=skip_refs,
            batches=batches,
            preview_items=preview_items,
            flow_diagnostics=flow_diag_dicts,
        )


def apply_loader_validation_success_to_session(
    result: LoaderValidationSuccess,
    api_key: str,
    org_id: str,
    *,
    org_label: str | None = None,
    working_config_json: str | None = None,
    generation_recipes: dict | None = None,
) -> SessionState:
    """Build a ``SessionState`` from a successful pipeline (router then persists draft)."""
    token = secrets.token_urlsafe(32)
    return SessionState(
        session_token=token,
        api_key=api_key,
        org_id=org_id,
        config=result.config,
        config_json_text=result.config_json_text,
        registry=result.registry,
        batches=result.batches,
        preview_items=result.preview_items,
        org_registry=result.org_registry,
        discovery=result.discovery,
        reconciliation=result.reconciliation,
        skip_refs=result.skip_refs,
        flow_ir=result.flow_irs,
        expanded_flows=result.expanded_flows,
        pattern_flow_ir=result.flow_irs,
        pattern_expanded_flows=result.expanded_flows,
        base_config_json=result.config_json_text,
        authoring_config_json=result.authoring_config_json,
        mermaid_diagrams=result.mermaid_diagrams,
        view_data_cache=result.view_data_cache,
        working_config_json=working_config_json or result.config_json_text,
        generation_recipes=generation_recipes or {},
        org_label=org_label,
        flow_diagnostics=result.flow_diagnostics or None,
    )


def loader_validation_failure_htmx_parts(failure: LoaderValidationFailure) -> tuple[str, str]:
    """Map failure to ``(title, detail)`` for ``partials/error_alert.html`` (HTMX).

    Uses the same logical content as § v1: ``v1_errors`` + ``v1_flow_diagnostic_dicts`` + ``v1_phase``.
    Falls back to splitting ``message`` on ``\\n`` only when ``v1_errors`` is empty.
    """
    errors = list(failure.v1_errors)
    if not errors:
        title, _, detail = failure.message.partition("\n")
        title = title.strip() or "Validation failed"
        detail = (detail.strip() or failure.message.strip() or title).strip()
        return title, detail

    first = errors[0]
    title = (first.message.strip() or first.code or "Validation failed").strip()
    lines: list[str] = []
    if failure.v1_phase:
        lines.append(f"Phase: {failure.v1_phase}")
    if first.path:
        lines.append(f"Primary error path: {first.path}")
    for e in errors[1:]:
        chunk = e.message.strip()
        if e.path:
            chunk += f" (path: {e.path})"
        if e.code and e.code not in chunk:
            chunk = f"{e.code}: {chunk}"
        lines.append(chunk)
    for d in failure.v1_flow_diagnostic_dicts:
        rid = d.get("rule_id", "")
        sev = d.get("severity", "")
        msg = d.get("message", "")
        step = d.get("step_id")
        tail = f" (step {step})" if step else ""
        lines.append(f"[{sev}] {rid}{tail}: {msg}")
    detail = "\n".join(lines) if lines else ""
    return title, detail


def loader_validation_failure_to_envelope(
    failure: LoaderValidationFailure,
) -> LoaderSetupEnvelopeV1:
    """§ v1 envelope for JSON clients (validate-json, revalidate-json, config/save)."""
    errors = list(failure.v1_errors)
    if not errors:
        errors = [
            LoaderSetupErrorItem(
                code="pipeline_error",
                message=failure.message.replace("\n", " ").strip(),
                path=None,
            )
        ]
    diagnostics = [
        LoaderSetupFlowDiagnosticItem.model_validate(d) for d in failure.v1_flow_diagnostic_dicts
    ]
    return LoaderSetupEnvelopeV1(
        ok=False,
        phase=failure.v1_phase,
        errors=errors,
        diagnostics=diagnostics,
        data={},
    )


def loader_validation_success_to_v1_envelope(
    result: LoaderValidationSuccess,
    *,
    extra_data: dict[str, Any] | None = None,
) -> LoaderSetupEnvelopeV1:
    diagnostics = [LoaderSetupFlowDiagnosticItem.model_validate(d) for d in result.flow_diagnostics]
    data: dict[str, Any] = {
        "resource_count": sum(len(b) for b in result.batches),
        "batch_count": len(result.batches),
        "has_funds_flows": bool(result.flow_irs),
    }
    if extra_data:
        data.update(extra_data)
    return LoaderSetupEnvelopeV1(
        ok=True,
        phase="complete",
        diagnostics=diagnostics,
        data=data,
    )
