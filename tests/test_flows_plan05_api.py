"""Plan 05: scenario-snapshot + recipe-patch APIs."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from dataloader.main import app
from dataloader.routers.setup._helpers import session_has_funds_flows_list
from dataloader.session import sessions
from flow_compiler import GenerationResult
from tests.test_flow_actor_config import _actor_flow_session


def test_session_has_funds_flows_list_pattern_only_with_recipes():
    """Revalidate redirect must match /flows: pattern IR counts before instance IR exists."""
    _, sess = _actor_flow_session()
    assert sess.flow_ir is None
    assert sess.pattern_flow_ir
    assert sess.generation_recipes
    assert session_has_funds_flows_list(sess) is True


def test_scenario_snapshot_all_recipes():
    token, sess = _actor_flow_session()
    sessions[token] = sess
    try:
        client = TestClient(app)
        r = client.post(
            "/api/flows/scenario-snapshot",
            headers={"X-Session-Token": token},
            json={},
        )
        assert r.status_code == 200
        data = r.json()
        assert "actor_test" in data["recipes"]
        assert "actor_test" in data["flow_refs"]
    finally:
        sessions.pop(token, None)


def test_scenario_snapshot_single_flow_ref():
    token, sess = _actor_flow_session()
    sessions[token] = sess
    try:
        client = TestClient(app)
        r = client.post(
            "/api/flows/scenario-snapshot",
            headers={"X-Session-Token": token},
            json={"flow_ref": "actor_test"},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["has_recipe"] is True
        assert data["recipe"]["flow_ref"] == "actor_test"
        assert data["default_recipe"]["flow_ref"] == "actor_test"
    finally:
        sessions.pop(token, None)


def test_scenario_snapshot_unauthorized():
    client = TestClient(app)
    r = client.post("/api/flows/scenario-snapshot", headers={"X-Session-Token": "nope"}, json={})
    assert r.status_code == 401


def test_recipe_patch_merge_seed():
    """Merge + validate; recompose is mocked (actor fixture DAG references unresolved ledger refs)."""
    token, sess = _actor_flow_session()
    sessions[token] = sess
    gen = GenerationResult(
        config=sess.config,
        diagrams=[],
        edge_case_map={},
        flow_irs=list(sess.pattern_flow_ir or []),
        expanded_flows=list(sess.pattern_expanded_flows or []),
    )
    try:
        with patch(
            "dataloader.routers.flows.api.recompose_and_persist_session",
            AsyncMock(return_value=gen),
        ):
            client = TestClient(app)
            r = client.post(
                "/api/flows/recipe-patch",
                headers={"X-Session-Token": token},
                json={"flow_ref": "actor_test", "patch": {"seed": 99999}},
            )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert data["recipe"]["seed"] == 99999
        assert sessions[token].generation_recipes["actor_test"]["seed"] == 99999
    finally:
        sessions.pop(token, None)


def test_recipe_patch_invalid_merge_422():
    token, sess = _actor_flow_session()
    sessions[token] = sess
    try:
        client = TestClient(app)
        r = client.post(
            "/api/flows/recipe-patch",
            headers={"X-Session-Token": token},
            json={"flow_ref": "actor_test", "patch": {"instances": 0}},
        )
        assert r.status_code == 422
        assert "Invalid merged recipe" in r.json().get("error", "")
    finally:
        sessions.pop(token, None)
