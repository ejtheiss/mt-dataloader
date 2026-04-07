"""Setup validate-json returns structured errors for dry_run ValueError (staged deps)."""

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from dataloader.main import app

_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "staged_verification_conflict.json"


@pytest.fixture
def staged_conflict_json() -> bytes:
    assert _FIXTURE.is_file(), f"missing {_FIXTURE}"
    return _FIXTURE.read_bytes()


def test_validate_json_staged_dependency_error(staged_conflict_json: bytes):
    with TestClient(app) as client:
        r = client.post("/api/validate-json", content=staged_conflict_json)
    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == 1
    assert data["ok"] is False
    assert data["phase"] == "dag"
    assert len(data["errors"]) == 1
    err = data["errors"][0]
    assert err["path"] == "(dag)"
    assert err["code"] == "staged_dependency"
    assert "staged resource" in err["message"].lower()
    assert "complete_verification" in err["message"].lower()
