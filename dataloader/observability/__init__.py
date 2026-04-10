"""Cross-cutting observability (tracing, optional metrics)."""

from __future__ import annotations

from dataloader.observability.loader_validation_trace import configure_loader_otel_from_env

__all__ = ["configure_loader_otel_from_env"]
