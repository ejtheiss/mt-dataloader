"""GET /webhooks/stream — admin vs user subscription rules (Option A)."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from dataloader.main import app
from dataloader.routers.deps import get_current_app_user
from models import CurrentAppUser


@pytest.fixture(autouse=True)
def clear_overrides():
    yield
    app.dependency_overrides.pop(get_current_app_user, None)


def test_non_admin_stream_without_run_id_returns_403(
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
        r = client.get("/webhooks/stream")
    assert r.status_code == 403


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
