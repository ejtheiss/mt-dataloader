"""GET /webhooks/stream — admin vs user subscription rules."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from starlette.testclient import TestClient

from dataloader.main import app
from dataloader.routers.deps import get_current_app_user
from dataloader.webhooks.buffer_state import WebhookEntry
from dataloader.webhooks.stream_fanout import _sse_entry_visible
from models import AppSettings, CurrentAppUser


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.pop(get_current_app_user, None)


def _sample_entry(*, run_id: str | None = "run-a") -> WebhookEntry:
    return WebhookEntry(
        received_at="2026-01-01T00:00:00+00:00",
        event_type="e",
        resource_type="t",
        resource_id="rid",
        webhook_id="wh",
        run_id=run_id,
        typed_ref=None,
        raw={},
    )


@pytest.mark.asyncio
async def test_sse_entry_visible_scoped_to_run_id_query() -> None:
    request = MagicMock()
    settings = MagicMock(spec=AppSettings)
    user = CurrentAppUser(id=1, role="user")
    entry = _sample_entry(run_id="run-a")
    assert await _sse_entry_visible(request, settings, entry, user, filter_run_id="run-a")
    assert not await _sse_entry_visible(request, settings, entry, user, filter_run_id="run-b")


@pytest.mark.asyncio
async def test_sse_entry_visible_admin_aggregate_includes_unmatched() -> None:
    request = MagicMock()
    settings = MagicMock(spec=AppSettings)
    admin = CurrentAppUser(id=1, role="admin")
    entry = _sample_entry(run_id=None)
    assert await _sse_entry_visible(request, settings, entry, admin, filter_run_id=None)


@pytest.mark.asyncio
async def test_sse_entry_visible_user_aggregate_hides_unmatched() -> None:
    request = MagicMock()
    settings = MagicMock(spec=AppSettings)
    user = CurrentAppUser(id=1, role="user")
    entry = _sample_entry(run_id=None)
    assert not await _sse_entry_visible(request, settings, entry, user, filter_run_id=None)


@pytest.mark.asyncio
async def test_sse_entry_visible_user_aggregate_uses_run_is_readable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = MagicMock()
    settings = MagicMock(spec=AppSettings)
    user = CurrentAppUser(id=1, role="user")
    entry = _sample_entry(run_id="run-z")
    monkeypatch.setattr(
        "dataloader.webhooks.stream_fanout.run_is_readable",
        AsyncMock(return_value=True),
    )
    assert await _sse_entry_visible(request, settings, entry, user, filter_run_id=None)


def test_non_admin_stream_for_unreadable_run_returns_404(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    runs = tmp_path / "runs"
    data.mkdir(parents=True)
    runs.mkdir()
    monkeypatch.setenv("DATALOADER_DATA_DIR", str(data))
    monkeypatch.setenv("DATALOADER_RUNS_DIR", str(runs))
    monkeypatch.setenv("DATALOADER_NGROK_AUTO_START", "false")

    app.dependency_overrides[get_current_app_user] = lambda: CurrentAppUser(id=1, role="user")
    with TestClient(app) as client:
        r = client.get("/webhooks/stream?run_id=definitely_missing_run_xyz")
    assert r.status_code == 404
