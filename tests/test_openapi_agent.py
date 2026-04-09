"""Plan 06: filtered OpenAPI for agent tooling."""

from starlette.testclient import TestClient

from dataloader.main import app


def test_openapi_agent_json_lists_tagged_routes_only():
    with TestClient(app) as client:
        r = client.get("/openapi-agent.json")
    assert r.status_code == 200
    data = r.json()
    assert data.get("openapi")
    assert "(agent)" in (data.get("info") or {}).get("title", "")
    paths = data.get("paths") or {}
    assert "/api/validate-json" in paths
    assert "post" in paths["/api/validate-json"]
    assert "/api/flows/scenario-snapshot" in paths
    assert "/runs" not in paths
    assert "/api/validate" not in paths


def test_full_openapi_includes_untagged_routes():
    with TestClient(app) as client:
        r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json().get("paths") or {}
    assert "/api/validate-json" in paths
    assert "/api/validate" in paths
