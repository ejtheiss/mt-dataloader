"""JSON API v1 envelope for ``/api/validate-json`` and ``/api/config/save`` (plan 04)."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from dataloader.main import app

_MINIMAL = Path(__file__).resolve().parent.parent / "examples" / "psp_minimal.json"


@pytest.fixture
def psp_minimal_bytes() -> bytes:
    assert _MINIMAL.is_file(), f"missing {_MINIMAL}"
    return _MINIMAL.read_bytes()


def test_validate_json_v1_success_envelope(psp_minimal_bytes: bytes):
    with TestClient(app) as client:
        r = client.post("/api/validate-json", content=psp_minimal_bytes)
    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == 1
    assert data["ok"] is True
    assert data["phase"] == "complete"
    assert data["errors"] == []
    assert data["warnings"] == []
    assert data["diagnostics"] == []
    assert "resource_count" in data["data"]
    assert "batch_count" in data["data"]
    assert "has_funds_flows" in data["data"]
    assert isinstance(data["data"]["resource_count"], int)
    assert data["data"]["has_funds_flows"] is True


def test_validate_json_v1_invalid_body_422():
    with TestClient(app) as client:
        r = client.post("/api/validate-json", content=b"not json")
    assert r.status_code == 422
    data = r.json()
    assert data["schema_version"] == 1
    assert data["ok"] is False
    assert len(data["errors"]) == 1
    assert data["errors"][0]["code"] == "invalid_body"


def test_validate_json_v1_parse_errors_use_code_not_type():
    # `{}` is a valid (empty) DataLoaderConfig; use a schema violation instead.
    with TestClient(app) as client:
        r = client.post(
            "/api/validate-json",
            content=b'{"connections": "must_be_a_list"}',
        )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["phase"] == "parse"
    assert data["errors"]
    assert "code" in data["errors"][0]
    assert "type" not in data["errors"][0]


def test_config_save_v1_unknown_session_404():
    with TestClient(app) as client:
        r = client.post(
            "/api/config/save",
            json={"session_token": "definitely-not-a-session", "config_json": "{}"},
        )
    assert r.status_code == 404
    data = r.json()
    assert data["schema_version"] == 1
    assert data["ok"] is False
    assert data["errors"][0]["code"] == "session_expired"
