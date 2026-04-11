"""Template context for the runs list HTMX fragment (Plan 06)."""

from __future__ import annotations

from typing import Any

from dataloader.handlers import DELETABILITY


def runs_list_fragment_context(
    *,
    rows: list[Any],
    sort: str | None,
    dir: str,
    status: str | None,
) -> dict[str, Any]:
    """Context for ``runs_page.html`` + ``block_name=runs_list`` (and the included list body)."""
    return {
        "title": "Runs",
        "run_rows": rows,
        "deletability": DELETABILITY,
        "sort_key": sort or "",
        "sort_dir": dir,
        "active_status": status or "",
    }
