"""Template context for setup preview routes (full page + HTMX redirect path)."""

from __future__ import annotations

from typing import Any

from dataloader.engine import config_hash
from dataloader.helpers import (
    build_available_connections,
    build_discovered_by_type,
    build_flow_grouped_preview,
)
from dataloader.session import SessionState
from models import DisplayPhase


def preview_resource_counts(sess: SessionState) -> tuple[int, int, int]:
    total_resources = sum(len(b) for b in sess.batches)
    deletable_count = sum(1 for i in sess.preview_items if i["deletable"])
    non_deletable_count = sum(1 for i in sess.preview_items if not i["deletable"])
    return total_resources, deletable_count, non_deletable_count


def flat_preview_template_context(sess: SessionState) -> dict[str, Any]:
    """Context for ``preview_page.html`` and HTMX ``preview.html`` (no funds flows)."""
    total_resources, deletable_count, non_deletable_count = preview_resource_counts(sess)
    return {
        "session_token": sess.session_token,
        "batches": sess.batches,
        "preview_items": sess.preview_items,
        "config_hash": config_hash(sess.config),
        "resource_count": total_resources,
        "deletable_count": deletable_count,
        "non_deletable_count": non_deletable_count,
        "display_phases": DisplayPhase,
        "discovery": sess.discovery,
        "reconciliation": sess.reconciliation,
        "config_json_text": sess.config_json_text,
        "discovered_by_type": build_discovered_by_type(sess.discovery),
        "has_funds_flows": False,
        "available_connections": build_available_connections(
            sess.config,
            sess.discovery,
        ),
    }


def flow_preview_template_context(sess: SessionState) -> dict[str, Any]:
    """Context for ``preview_flows_page.html``."""
    total_resources, deletable_count, non_deletable_count = preview_resource_counts(sess)
    flow_groups = build_flow_grouped_preview(sess)
    return {
        "session_token": sess.session_token,
        "flow_groups": flow_groups,
        "resource_count": total_resources,
        "deletable_count": deletable_count,
        "non_deletable_count": non_deletable_count,
        "discovery": sess.discovery,
        "reconciliation": sess.reconciliation,
        "config_json_text": sess.config_json_text,
        "has_funds_flows": True,
        "mermaid_diagrams": sess.mermaid_diagrams or [],
        "available_connections": build_available_connections(
            sess.config,
            sess.discovery,
        ),
    }
