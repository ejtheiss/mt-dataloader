"""POST /api/draft/discard removes loader_drafts row (Wave D explicit discard)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient


def test_discard_loader_draft_redirects_and_clears_row(
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
        client.get("/setup")
        db_path = data / "dataloader.sqlite"
        assert db_path.is_file()
        draft_json = json.dumps(
            {
                "schema_version": 1,
                "org_id": "",
                "config_json_text": "{}",
                "batches": [],
                "preview_items": [],
                "generation_recipes": {},
                "mermaid_diagrams": [],
                "skip_refs": [],
                "update_refs": {},
                "payload_overrides": [],
            }
        )
        import sqlite3

        con = sqlite3.connect(str(db_path))
        con.execute(
            "INSERT INTO loader_drafts (user_id, draft_json, updated_at) VALUES (1, ?, ?)",
            (draft_json, "2026-04-03T00:00:00+00:00"),
        )
        con.commit()
        assert con.execute("SELECT COUNT(*) FROM loader_drafts").fetchone()[0] == 1
        con.close()

        r = client.post("/api/draft/discard", follow_redirects=False)
        assert r.status_code == 303
        assert r.headers.get("location", "").endswith("/setup")

        con = sqlite3.connect(str(db_path))
        assert con.execute("SELECT COUNT(*) FROM loader_drafts").fetchone()[0] == 0
        con.close()
