"""Wave B: GET /api/runs uses SQL only when DB is up (no post-startup disk glob)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

_MIN_MANIFEST = {
    "run_id": "20260420T120000_deadbeef",
    "config_hash": "sha256:00",
    "started_at": "2026-04-20T12:00:00+00:00",
    "status": "completed",
    "resources_created": [],
    "resources_failed": [],
    "resources_staged": [],
}


def test_get_api_runs_sql_only_ignores_unmirrored_disk_manifest(
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

    from dataloader.main import app

    with TestClient(app) as client:
        (runs / "20260420T120000_deadbeef.json").write_text(
            json.dumps(_MIN_MANIFEST),
            encoding="utf-8",
        )
        r = client.get("/api/runs")
    assert r.status_code == 200
    assert "20260420T120000_deadbeef" not in r.text
