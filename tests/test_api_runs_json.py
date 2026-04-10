"""Plan 09: GET /api/runs.json — SQL-backed JSON list and pagination."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from starlette.testclient import TestClient

from models import RunListJsonResponse


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


async def _seed_n_runs_iso(
    factory: Any,
    run_ids: list[str],
    *,
    mt_org_id: str,
    month: int = 6,
    day_start: int = 10,
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
                status="completed",
                config_hash=None,
                started_at=f"2026-{month:02d}-{day_start + i:02d}T12:00:00+00:00",
                completed_at="2026-06-10T13:00:00+00:00",
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
        assert data["next_cursor"] is None
        assert len(data["items"]) == 2
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


def test_get_api_runs_json_keyset_cursor_matches_offset_pages() -> None:
    from dataloader.main import app
    from dataloader.runs_pagination import encode_runs_seek_cursor

    u = uuid.uuid4().hex[:8]
    r0, r1, r2 = f"rk_{u}_0", f"rk_{u}_1", f"rk_{u}_2"
    org = f"org_keyset_{u}"
    common = {"mt_org_id": org}

    with TestClient(app) as client:
        factory = app.state.async_session_factory
        asyncio.run(_seed_three_runs(factory, (r0, r1, r2), mt_org_id=org))

        p1 = client.get("/api/runs.json", params={"limit": 2, **common}).json()
        assert p1["has_more"] is True
        assert p1["next_cursor"] is None
        assert p1["items"][0]["run_id"] == r2
        assert p1["items"][1]["run_id"] == r1

        sa1 = p1["items"][1]["started_at"]
        rid1 = p1["items"][1]["run_id"]
        cur = encode_runs_seek_cursor(sa1, rid1)

        p_key = client.get("/api/runs.json", params={"limit": 2, "cursor": cur, **common}).json()
    assert p_key["offset"] == 0
    assert p_key["items"][0]["run_id"] == r0
    assert p_key["has_more"] is False
    assert p_key["next_cursor"] is None


def test_get_api_runs_json_cursor_validation() -> None:
    from dataloader.main import app

    with TestClient(app) as client:
        r = client.get("/api/runs.json", params={"cursor": "not-a-token", "limit": 5})
        assert r.status_code == 400

        r2 = client.get("/api/runs.json", params={"cursor": "e30=", "limit": 5})
        assert r2.status_code == 400

        r3 = client.get("/api/runs.json", params={"cursor": "abc", "offset": 3, "limit": 5})
        assert r3.status_code == 400

        r4 = client.get("/api/runs.json", params={"sort": "status", "limit": 5})
        assert r4.status_code == 200
        # cursor + column sort
        from dataloader.runs_pagination import encode_runs_seek_cursor

        c = encode_runs_seek_cursor("2026-01-01T00:00:00+00:00", "x")
        r5 = client.get("/api/runs.json", params={"sort": "status", "cursor": c, "limit": 5})
        assert r5.status_code == 400


def test_get_api_runs_json_invalid_sort() -> None:
    from dataloader.main import app

    with TestClient(app) as client:
        r = client.get("/api/runs.json", params={"sort": "nope", "limit": 5})
    assert r.status_code == 422


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
    assert "items" in props and "has_more" in props and "next_cursor" in props


def test_runs_json_response_validates_as_model() -> None:
    from dataloader.main import app

    with TestClient(app) as client:
        r = client.get("/api/runs.json", params={"limit": 5, "offset": 0})
    assert r.status_code == 200
    RunListJsonResponse.model_validate(r.json())


@pytest.mark.parametrize("limit,offset", [(1, 0), (20, 0), (50, 0), (10, 5), (100, 0)])
def test_runs_json_openapi_contract_parametrize(limit: int, offset: int) -> None:
    """Light contract check (Plan 00 §6.6 style) without schemathesis ASGI lifespan issues."""
    from dataloader.main import app

    with TestClient(app) as client:
        r = client.get("/api/runs.json", params={"limit": limit, "offset": offset})
    assert r.status_code == 200, r.text
    RunListJsonResponse.model_validate(r.json())


def test_keyset_mode_returns_next_cursor_when_more_pages() -> None:
    from dataloader.main import app
    from dataloader.runs_pagination import encode_runs_seek_cursor

    u = uuid.uuid4().hex[:8]
    ids = [f"r5_{u}_{i}" for i in range(5)]
    org = f"org_5_{u}"

    with TestClient(app) as client:
        factory = app.state.async_session_factory
        asyncio.run(_seed_n_runs_iso(factory, ids, mt_org_id=org))

        p1 = client.get("/api/runs.json", params={"limit": 2, "mt_org_id": org}).json()
        assert p1["has_more"] is True
        assert p1["next_cursor"] is None

        last = p1["items"][-1]
        cur = encode_runs_seek_cursor(last["started_at"], last["run_id"])
        p2 = client.get(
            "/api/runs.json",
            params={"limit": 2, "cursor": cur, "mt_org_id": org},
        ).json()
        assert p2["has_more"] is True
        assert p2["next_cursor"] is not None
