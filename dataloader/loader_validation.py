"""Pure loader validation steps shared by setup routes (no FastAPI).

Used by ``validate_json`` (headless JSON API) and ``_validate_pipeline`` (full setup
validate / revalidate). Plan 04 — loader pipeline compose.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from graphlib import CycleError
from typing import cast

from pydantic import ValidationError

from dataloader.engine import dry_run
from flow_compiler import AuthoringConfig, ExecutionPlan, compile_to_plan
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
