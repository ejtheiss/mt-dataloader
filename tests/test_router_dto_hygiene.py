"""Plan mapper boundary: view DTOs are not constructed in ``dataloader/routers``."""

from __future__ import annotations

from pathlib import Path

import pytest

_ROUTERS_DIR = Path(__file__).resolve().parent.parent / "dataloader" / "routers"

# View DTOs from ``models.run_views`` — build only in db layer / engine summaries.
_FORBIDDEN = (
    "CreatedResourceRow(",
    "RunDetailView(",
    "FailedResourceRow(",
    "StagedItemView(",
    "RunExecuteSummaryDTO(",
)


@pytest.mark.parametrize("pattern", _FORBIDDEN)
def test_router_modules_do_not_instantiate_view_dtos(pattern: str) -> None:
    assert _ROUTERS_DIR.is_dir(), f"missing {_ROUTERS_DIR}"
    hits: list[str] = []
    for path in sorted(_ROUTERS_DIR.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if pattern in text:
            hits.append(f"{path.name}: contains {pattern!r}")
    assert not hits, ";\n".join(hits)
