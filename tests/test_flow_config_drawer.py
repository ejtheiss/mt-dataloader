"""Plan 10e — config drawer partial + JSON context."""

from __future__ import annotations

from starlette.testclient import TestClient

from dataloader.main import app
from dataloader.session import sessions
from tests.test_flow_actor_config import _actor_flow_session


def _session_for_config_drawer():
    token, sess = _actor_flow_session()
    sess.flow_ir = list(sess.pattern_flow_ir or [])
    sess.expanded_flows = list(sess.pattern_expanded_flows or [])
    return token, sess


def test_config_drawer_get_ok():
    token, sess = _session_for_config_drawer()
    sessions[token] = sess
    try:
        client = TestClient(app)
        r = client.get(
            f"/api/flows/0/config-drawer?session_token={token}",
        )
        assert r.status_code == 200
        assert 'data-flow-config-drawer="1"' in r.text
        assert "Band 1" in r.text
        assert "Band 5" in r.text
    finally:
        sessions.pop(token, None)


def test_config_drawer_json_ok():
    token, sess = _session_for_config_drawer()
    sessions[token] = sess
    try:
        client = TestClient(app)
        r = client.get(
            f"/api/flows/0/config?session_token={token}",
        )
        assert r.status_code == 200
        data = r.json()
        assert data["flow_ref"] == "actor_test"
        assert "config_version" in data
        assert data["session_token"] == token
    finally:
        sessions.pop(token, None)


def test_config_drawer_not_found():
    client = TestClient(app)
    r = client.get("/api/flows/99/config-drawer?session_token=nope")
    assert r.status_code == 404
