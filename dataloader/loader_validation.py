"""Loader validation: pure steps + full async pipeline (no FastAPI).

Headless JSON API (``validate_json``), full setup validate/revalidate
(``run_loader_validation_pipeline`` → ``apply_loader_validation_success_to_session``),
and shared Pydantic parse helpers for ``flows.py``. Plan 04 — Wave D + Phase 2 dedupe.
"""

from __future__ import annotations

import hashlib
import secrets
from collections import Counter
from dataclasses import asdict, dataclass, field
from graphlib import CycleError
from typing import Any, TypeVar, cast

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
    LoaderSetupErrorItem,
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


@dataclass(frozen=True)
class ParseLoaderOutcome:
    """Result of parsing raw bytes as ``DataLoaderConfig`` JSON."""

    config: DataLoaderConfig | None = None
    error: ValidationError | None = None


def parse_loader_config_bytes(raw_json: bytes) -> ParseLoaderOutcome:
    try:
        return ParseLoaderOutcome(config=DataLoaderConfig.model_validate_json(raw_json))
    except ValidationError as e:
        return ParseLoaderOutcome(error=e)


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
    """Outcome for ``POST /api/validate-json`` body (after UTF-8 JSON object pre-check)."""

    ok: bool
    phase: LoaderSetupPhase | None
    errors: list[LoaderSetupErrorItem]
    data: dict


def authoring_config_from_bytes(config: DataLoaderConfig, raw_json: bytes) -> AuthoringConfig:
    return AuthoringConfig(
        config=config.model_copy(deep=True),
        json_text=raw_json.decode(),
        source_hash=hashlib.sha256(raw_json).hexdigest(),
    )


def run_headless_validate_json(raw_json: bytes) -> HeadlessValidateJsonOutcome:
    """Parse → headless compile pipeline → ``dry_run(config)``; map failures to v1 ErrorItems."""
    parsed = parse_loader_config_bytes(raw_json)
    if parsed.error is not None:
        return HeadlessValidateJsonOutcome(
            ok=False,
            phase="parse",
            errors=error_items_from_pydantic_validation(parsed.error),
            data={},
        )
    config = cast(DataLoaderConfig, parsed.config)
    had_funds_flows = bool(config.funds_flows)

    authoring = authoring_config_from_bytes(config, raw_json)
    compiled = compile_loader_plan(authoring, pipeline=HEADLESS_VALIDATE_JSON_PIPELINE)
    if isinstance(compiled, LoaderCompileFailure):
        return HeadlessValidateJsonOutcome(
            ok=False,
            phase="compile",
            errors=list(compiled.errors),
            data={},
        )

    config = compiled.config
    dry = try_loader_dry_run(config)
    if isinstance(dry, LoaderDryRunFailure):
        return HeadlessValidateJsonOutcome(
            ok=False,
            phase="dag",
            errors=list(dry.errors),
            data={},
        )

    batches = dry.batches
    return HeadlessValidateJsonOutcome(
        ok=True,
        phase="complete",
        errors=[],
        data={
            "resource_count": sum(len(b) for b in batches),
            "batch_count": len(batches),
            "has_funds_flows": had_funds_flows,
        },
    )


# ---------------------------------------------------------------------------
# Full loader pipeline (validate / revalidate) — Wave D validate → apply → persist
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoaderValidationFailure:
    """Structured failure from ``run_loader_validation_pipeline`` (HTML title\\nbody in ``message``)."""

    message: str


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
    parsed = parse_loader_config_bytes(raw_json)
    if parsed.error is not None:
        structured = format_validation_errors(parsed.error)
        detail_lines = [f"• {err['path']}: {err['message']}" for err in structured]
        return LoaderValidationFailure(
            message="Config Validation Error\n" + ("\n".join(detail_lines) or str(parsed.error)),
        )

    if parsed.config is None:
        return LoaderValidationFailure(message="Config Validation Error\nInvalid configuration.")
    config = parsed.config
    authoring_config_json = config.model_dump_json(indent=2, exclude_none=True)

    authoring = authoring_config_from_bytes(config, raw_json)
    compiled = compile_loader_plan(authoring)
    if isinstance(compiled, LoaderCompileFailure):
        return LoaderValidationFailure(message=compiled.pipeline_message)

    plan = compiled
    config = plan.config
    flow_irs = list(plan.flow_irs)
    expanded_flows = list(plan.expanded_flows)
    mermaid_diagrams = list(plan.mermaid_diagrams) if plan.mermaid_diagrams else None
    view_data_cache = list(plan.view_data) if plan.view_data else None

    flow_diag_dicts: list[dict] = []
    if len(flow_irs) != len(expanded_flows):
        logger.warning(
            "flow_irs / expanded_flows length mismatch: {} vs {}",
            len(flow_irs),
            len(expanded_flows),
        )
    for ir, fc in zip(flow_irs, expanded_flows):
        for d in validate_flow(ir, actor_refs=flatten_actor_refs(fc.actors)):
            flow_diag_dicts.append(asdict(d))
    if flow_diag_dicts:
        by_rule = Counter(d["rule_id"] for d in flow_diag_dicts)
        logger.debug(
            "Flow advisory diagnostics: {} finding(s) by_rule={}",
            len(flow_diag_dicts),
            dict(by_rule),
        )

    discovery: DiscoveryResult | None = None
    org_registry: OrgRegistry | None = None
    async with AsyncModernTreasury(api_key=api_key, organization_id=org_id) as client:
        try:
            await client.ping()
        except AuthenticationError:
            return LoaderValidationFailure(
                message="Authentication Error\nInvalid API key or org ID"
            )
        try:
            discovery = await discover_org(client, config=config)
            org_registry = OrgRegistry.from_discovery(discovery)
        except (APIConnectionError, APITimeoutError) as exc:
            logger.warning("Discovery failed: {}", str(exc))

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

    dry = try_loader_dry_run(config, known_refs, skip_refs=skip_refs)
    if isinstance(dry, LoaderDryRunFailure):
        return LoaderValidationFailure(message=dry.pipeline_message)
    batches = dry.batches

    resource_map = {typed_ref_for(r): r for r in all_resources(config)}
    preview_items = build_preview(
        batches,
        resource_map,
        skip_refs=skip_refs,
        reconciliation=reconciliation,
    )

    config_json_text = config.model_dump_json(indent=2, exclude_none=True)

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
