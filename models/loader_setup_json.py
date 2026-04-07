"""Loader setup JSON API contract (v1) — ``POST /api/validate-json``, ``POST /api/config/save``.

Normative spec: ``plan/…/04_validation_observability.md`` § Loader setup — JSON API contract (v1).
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

LOADER_SETUP_SCHEMA_VERSION: Literal[1] = 1

LoaderSetupPhase = Literal["parse", "compile", "discover", "reconcile", "dag", "complete"]


class LoaderSetupErrorItem(BaseModel):
    """v1 ErrorItem — stable ``code`` for machines (Pydantic ``type`` for schema errors)."""

    code: str
    message: str
    path: str | None = None


class LoaderSetupWarningItem(BaseModel):
    code: str
    message: str


class LoaderSetupFlowDiagnosticItem(BaseModel):
    """Same shape as ``flow_compiler.flow_validator.FlowDiagnostic`` / ``dataclasses.asdict``."""

    rule_id: str
    severity: Literal["error", "warning", "info"]
    step_id: str | None
    account_id: str | None
    message: str


class LoaderSetupEnvelopeV1(BaseModel):
    """Shared response envelope for validate-json and config/save (schema_version 1)."""

    schema_version: Literal[1] = 1
    ok: bool
    phase: LoaderSetupPhase | None = None
    errors: list[LoaderSetupErrorItem] = Field(default_factory=list)
    warnings: list[LoaderSetupWarningItem] = Field(default_factory=list)
    diagnostics: list[LoaderSetupFlowDiagnosticItem] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)

    def json_response_dict(self) -> dict[str, Any]:
        """Serialize for FastAPI/Starlette JSONResponse (JSON-compatible, omit null ``phase`` only)."""
        return self.model_dump(mode="json", exclude_none=True)


def error_items_from_pydantic_validation(exc: ValidationError) -> list[LoaderSetupErrorItem]:
    """Map Pydantic ``ValidationError`` rows to v1 ErrorItems (``code`` = Pydantic ``type``)."""
    items: list[LoaderSetupErrorItem] = []
    for err in exc.errors():
        loc = err.get("loc") or ()
        path = _format_loc(loc) if loc else None
        items.append(
            LoaderSetupErrorItem(
                code=str(err.get("type", "validation_error")),
                message=str(err.get("msg", "Validation error")),
                path=path,
            )
        )
    return items


def _format_loc(loc: tuple[Any, ...]) -> str:
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


def parse_request_json_body(raw: bytes) -> dict[str, Any] | None:
    """Return decoded JSON object, or ``None`` if body is not a JSON object (caller returns 422)."""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        val = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(val, dict):
        return None
    return val
