"""OpenTelemetry spans for loader validation (full pipeline + headless).

Plan 04 — pipeline phase visibility. Does not log or tag API keys.

If ``opentelemetry-api`` is not installed, tracing is a no-op so the app still
starts (install deps from ``requirements.txt`` for real spans).

Enable console export locally::

    DATALOADER_OTEL_CONSOLE=1

Or use standard OTLP / other exporters by setting ``TracerProvider`` before app
startup (this module only wires console when the env flag is set).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from loguru import logger

try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace import Span, Status as OtelStatus, StatusCode as OtelStatusCode

    _HAVE_OTEL_API = True
except ModuleNotFoundError:  # pragma: no cover — exercised in minimal venvs
    otel_trace = None  # type: ignore[assignment]
    Span = Any  # type: ignore[misc, assignment]
    _HAVE_OTEL_API = False

    class _FallbackStatusCode:
        ERROR = object()
        OK = object()

    class _FallbackStatus:
        def __init__(self, status_code: Any, description: str | None = None) -> None:
            pass

    OtelStatus = _FallbackStatus  # type: ignore[misc, assignment]
    OtelStatusCode = _FallbackStatusCode  # type: ignore[misc, assignment]

# Re-export for ``loader_validation`` (avoids a direct opentelemetry import there).
Status = OtelStatus
StatusCode = OtelStatusCode

TRACER_NAME = "dataloader.loader_validation"
TRACER_VERSION = "1.0.0"


class _NoOpSpan:
    def set_attribute(self, key: str, value: Any) -> None:
        del key, value

    def set_status(self, status: Any) -> None:
        del status

    def record_exception(self, exc: BaseException) -> None:
        del exc


class _NoOpSpanCtx:
    def __enter__(self) -> _NoOpSpan:
        return _NoOpSpan()

    def __exit__(self, *args: Any) -> None:
        return None


class _NoOpTracer:
    def start_as_current_span(self, name: str, *args: Any, **kwargs: Any) -> _NoOpSpanCtx:
        del name, args, kwargs
        return _NoOpSpanCtx()


def loader_validation_tracer() -> Any:
    if _HAVE_OTEL_API and otel_trace is not None:
        return otel_trace.get_tracer(TRACER_NAME, TRACER_VERSION)
    return _NoOpTracer()


@contextmanager
def loader_span(name: str, **attributes: Any) -> Iterator[Any]:
    """Start a child span; log phase boundaries at DEBUG."""
    tracer = loader_validation_tracer()
    with tracer.start_as_current_span(name, attributes=attributes) as span:
        logger.debug("loader_validation span start {}", name)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        finally:
            logger.debug("loader_validation span end {}", name)


def configure_loader_otel_from_env() -> None:
    """If ``DATALOADER_OTEL_CONSOLE`` is truthy, export traces to stderr (dev / CI)."""
    flag = os.environ.get("DATALOADER_OTEL_CONSOLE", "").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return

    if not _HAVE_OTEL_API or otel_trace is None:
        logger.warning(
            "DATALOADER_OTEL_CONSOLE is set but opentelemetry-api is not installed; "
            "pip install -r requirements.txt"
        )
        return

    try:
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
    except ModuleNotFoundError:
        logger.warning(
            "DATALOADER_OTEL_CONSOLE requires opentelemetry-sdk; pip install -r requirements.txt"
        )
        return

    resource = Resource.create({"service.name": "mt-dataloader"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    otel_trace.set_tracer_provider(provider)
    logger.info("OpenTelemetry: console span export enabled (DATALOADER_OTEL_CONSOLE)")
