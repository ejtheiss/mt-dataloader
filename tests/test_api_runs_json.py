"""Plan 09: GET /api/runs.json — SQL-backed JSON list and pagination."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from starlette.testclient import TestClient


async def _seed_three_runs(
    factory: Any,
    run_ids: tuple[str, str, str],
    *,
    mt_org_id: str,
) -> None:
    from db.repositories import runs as runs_repo

    async with factory() as s:
        for i, rid in enumerate(run_ids):
            await runs_repo.backfill_upsert_run(
                s,
                run_id=rid,
                user_id=1,
                mt_org_id=mt_org_id,
                mt_org_label=None,
                status="completed" if i < 2 else "running",
                config_hash=None,
                started_at=f"2026-05-{10 + i:02d}T12:00:00+00:00",
                completed_at="2026-05-10T13:00:00+00:00" if i < 2 else None,
            )
        await s.commit()


def test_get_api_runs_json_pagination_and_status_filter() -> None:
    from dataloader.main import app

    u = uuid.uuid4().hex[:8]
    r0, r1, r2 = f"rj_{u}_0", f"rj_{u}_1", f"rj_{u}_2"
    org = f"org_json_isolation_{u}"
    common = {"mt_org_id": org}

    with TestClient(app) as client:
        factory = app.state.async_session_factory
        asyncio.run(_seed_three_runs(factory, (r0, r1, r2), mt_org_id=org))

        r = client.get("/api/runs.json", params={"limit": 2, "offset": 0, **common})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["limit"] == 2
        assert data["offset"] == 0
        assert data["has_more"] is True
        assert len(data["items"]) == 2
        # Newest ``started_at`` first
        assert data["items"][0]["run_id"] == r2
        assert data["items"][1]["run_id"] == r1

        r2p = client.get("/api/runs.json", params={"limit": 2, "offset": 2, **common})
        assert r2p.status_code == 200
        p2 = r2p.json()
        assert p2["has_more"] is False
        assert len(p2["items"]) == 1
        assert p2["items"][0]["run_id"] == r0

        rf = client.get("/api/runs.json", params={"status": "completed", **common})
        assert rf.status_code == 200
        completed_ids = {x["run_id"] for x in rf.json()["items"]}
        assert r0 in completed_ids and r1 in completed_ids
        assert r2 not in completed_ids


def test_get_api_runs_json_requires_database(monkeypatch: pytest.MonkeyPatch) -> None:
    from dataloader.main import app

    with TestClient(app) as client:
        monkeypatch.setattr(client.app.state, "async_session_factory", None)
        r = client.get("/api/runs.json")
    assert r.status_code == 503
    assert "database" in r.json()["detail"].lower()


def test_full_openapi_lists_runs_json_route() -> None:
    from dataloader.main import app

    with TestClient(app) as client:
        r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json().get("paths") or {}
    assert "/api/runs.json" in paths
    assert "get" in paths["/api/runs.json"]


def test_openapi_runs_json_response_schema() -> None:
    from dataloader.main import app

    with TestClient(app) as client:
        r = client.get("/openapi.json")
    assert r.status_code == 200
    schema = r.json().get("components", {}).get("schemas", {})
    assert "RunListJsonResponse" in schema
    props = schema["RunListJsonResponse"].get("properties") or {}
    assert "items" in props and "has_more" in props
