"""ASGI entry shim — canonical FastAPI app lives in ``dataloader.main``.

Prefer: ``uvicorn dataloader.main:app`` (see Dockerfile / Makefile).
This module re-exports ``app`` for compatibility with ``uvicorn main:app``.
"""

from __future__ import annotations

from dataloader.main import app

__all__ = ["app"]
