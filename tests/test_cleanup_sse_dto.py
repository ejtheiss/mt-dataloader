"""Cleanup SSE partials accept ``CreatedResourceRow`` (DTO), not manifest objects."""

from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

from models.run_views import CreatedResourceRow


@pytest.fixture
def templates_env() -> Environment:
    root = Path(__file__).resolve().parent.parent / "templates"
    return Environment(
        loader=FileSystemLoader(str(root)),
        autoescape=select_autoescape(["html", "xml"]),
    )


def test_cleanup_row_renders_created_resource_row(templates_env: Environment) -> None:
    entry = CreatedResourceRow(
        batch=0,
        resource_type="ledger",
        typed_ref="ledgers.main",
        created_id="la_abc",
        created_at="2026-01-01T00:00:00+00:00",
        deletable=True,
    )
    html = templates_env.get_template("partials/cleanup_row.html").render(
        entry=entry, action="deleted", status="success"
    )
    assert "ledgers.main" in html
    assert "ledger" in html
    assert "Deleted" in html


def test_run_complete_renders_execute_summary_dto(templates_env: Environment) -> None:
    from models.run_views import RunExecuteSummaryDTO

    summary = RunExecuteSummaryDTO(
        run_id="run_xyz",
        created_count=2,
        staged_count=1,
        failed_count=0,
        has_staged=True,
    )
    html = templates_env.get_template("partials/run_complete.html").render(
        summary=summary, run_id="run_xyz"
    )
    assert "2 created" in html
    assert "1 staged" in html
    assert "run_xyz" in html
    assert "Fire Staged" in html
