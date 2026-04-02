"""Execution engine for the Modern Treasury Dataloader.

Implementation is split under ``dataloader.engine`` submodules; this package
re-exports the public API unchanged for callers.
"""

from __future__ import annotations

from models import RunManifest

from .dag import build_dag, dry_run, inject_legal_entity_psp_connection_id
from .refs import (
    RefRegistry,
    all_resources,
    extract_ref_dependencies,
    resolve_refs,
    typed_ref_for,
)
from .run_meta import _MANIFEST_RE, _now_iso, config_hash, generate_run_id, list_manifest_ids
from .runner import ExecutionPhaseError, execute

__all__ = [
    "ExecutionPhaseError",
    "RefRegistry",
    "extract_ref_dependencies",
    "resolve_refs",
    "typed_ref_for",
    "all_resources",
    "build_dag",
    "dry_run",
    "generate_run_id",
    "execute",
    "config_hash",
    "RunManifest",
    "list_manifest_ids",
    "_now_iso",
    "inject_legal_entity_psp_connection_id",
    "_MANIFEST_RE",
]
