"""BFF-style view context builders (Plan 06 — grow with Plan 0 waves)."""

from dataloader.view_models.runs_list import runs_list_fragment_context
from dataloader.view_models.setup_preview import (
    flat_preview_template_context,
    flow_preview_template_context,
    preview_resource_counts,
)

__all__ = [
    "flat_preview_template_context",
    "flow_preview_template_context",
    "preview_resource_counts",
    "runs_list_fragment_context",
]
