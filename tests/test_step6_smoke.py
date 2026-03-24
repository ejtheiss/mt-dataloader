"""Step 6 smoke tests: routes, templates, SSE attributes."""
from __future__ import annotations

import os

os.environ.setdefault("MT_BASELINE_PATH", "baseline.yaml")

import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


class TestRoutes:
    def test_root_redirects(self, client):
        r = client.get("/", follow_redirects=False)
        assert r.status_code == 307

    def test_setup_200(self, client):
        r = client.get("/setup")
        assert r.status_code == 200
        assert "hx-post" in r.text and "api/validate" in r.text
        assert "MT Dataloader" in r.text

    def test_setup_has_css(self, client):
        r = client.get("/setup")
        assert "/static/style.css" in r.text

    def test_setup_has_htmx(self, client):
        r = client.get("/setup")
        assert "htmx.org" in r.text

    def test_setup_has_sse(self, client):
        r = client.get("/setup")
        assert "htmx-ext-sse" in r.text or "sse.js" in r.text

    def test_runs_200(self, client):
        r = client.get("/runs")
        assert r.status_code == 200
        assert "hx-trigger" in r.text and "load" in r.text
        assert 'name="api_key"' in r.text and 'name="org_id"' in r.text

    def test_api_runs_200(self, client):
        r = client.get("/api/runs")
        assert r.status_code == 200

    def test_static_css(self, client):
        r = client.get("/static/style.css")
        assert r.status_code == 200
        assert "@keyframes spin" in r.text
        assert "type-connection" in r.text

    def test_execute_expired_session(self, client):
        r = client.post("/api/execute", data={"session_token": "invalid"})
        assert r.status_code == 200  # HTMX convention: error HTML returned as 200
        assert "Session Expired" in r.text

    def test_execute_stream_expired(self, client):
        r = client.get("/api/execute/stream?session_token=invalid")
        assert r.status_code == 200
        assert "event: error" in r.text
        assert "event: close" in r.text

    def test_cleanup_stream_expired(self, client):
        r = client.get("/api/cleanup/stream/invalid-token")
        assert r.status_code == 200
        assert "event: error" in r.text

    def test_cleanup_missing_run(self, client):
        r = client.post(
            "/api/cleanup/nonexistent",
            data={"api_key": "test", "org_id": "test"},
        )
        assert r.status_code == 404


class TestMetadataEndpoint:
    def test_metadata_no_session(self, client):
        r = client.post(
            "/api/flows/0/metadata",
            json={"trace_key": "invoice_id"},
            headers={"X-Session-Token": "nonexistent"},
        )
        assert r.status_code == 401

    def test_metadata_updates_working_config(self, client):
        import json
        from session import SessionState, sessions
        from engine import RefRegistry
        from models import DataLoaderConfig

        config_data = {
            "funds_flows": [{
                "ref": "test_flow",
                "pattern_type": "psp",
                "trace_key": "deal_id",
                "trace_value_template": "{ref}-{instance}",
                "trace_metadata": {"env": "sandbox"},
                "steps": [
                    {"step_id": "lt1", "type": "ledger_transaction",
                     "ledger_entries": [
                         {"amount": 100, "direction": "debit",
                          "ledger_account_id": "$ref:ledger_account.cash"},
                         {"amount": 100, "direction": "credit",
                          "ledger_account_id": "$ref:ledger_account.rev"},
                     ]},
                ],
            }],
        }
        config = DataLoaderConfig.model_validate(config_data)
        token = "test-meta-token"
        sessions[token] = SessionState(
            session_token=token,
            api_key="k",
            org_id="o",
            config=config,
            config_json_text=json.dumps(config_data),
            registry=RefRegistry(),
            batches=[],
            working_config_json=json.dumps(config_data),
        )
        try:
            r = client.post(
                "/api/flows/0/metadata",
                json={
                    "trace_key": "invoice_id",
                    "trace_value_template": "INV-{instance}",
                    "trace_metadata": {"env": "production", "region": "us-east"},
                },
                headers={"X-Session-Token": token},
            )
            assert r.status_code == 200
            assert r.json()["status"] == "ok"

            updated = json.loads(sessions[token].working_config_json)
            flow = updated["funds_flows"][0]
            assert flow["trace_key"] == "invoice_id"
            assert flow["trace_value_template"] == "INV-{instance}"
            assert flow["trace_metadata"]["region"] == "us-east"
        finally:
            sessions.pop(token, None)
