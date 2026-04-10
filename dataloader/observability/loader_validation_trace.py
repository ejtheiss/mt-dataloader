"""OpenTelemetry spans for loader validation (full pipeline + headless).

Plan 04 — pipeline phase visibility. Does not log or tag API keys.

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
from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

TRACER_NAME = "dataloader.loader_validation"
TRACER_VERSION = "1.0.0"


def loader_validation_tracer() -> trace.Tracer:
    return trace.get_tracer(TRACER_NAME, TRACER_VERSION)


@contextmanager
def loader_span(name: str, **attributes: Any) -> Iterator[Span]:
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

    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

    resource = Resource.create({"service.name": "mt-dataloader"})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    logger.info("OpenTelemetry: console span export enabled (DATALOADER_OTEL_CONSOLE)")
