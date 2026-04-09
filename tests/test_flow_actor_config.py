"""Per-actor config drawer API (GET partial + POST merge + recompose)."""

from __future__ import annotations

from starlette.testclient import TestClient

from dataloader.engine import RefRegistry
from dataloader.session import SessionState, sessions
from flow_compiler.core import compile_flows
from main import app
from models import DataLoaderConfig, GenerationRecipeV1


def _make_actor_flow_config() -> dict:
    """Same shape as tests.test_seed_datasets._make_actor_flow_config."""
    return {
        "funds_flows": [
            {
                "ref": "actor_test",
                "pattern_type": "demo",
                "actors": {
                    "buyer": {
                        "alias": "buyer",
                        "name_template": "{business_name} LLC",
                        "slots": {},
                    },
                    "seller": {
                        "alias": "seller",
                        "customer_name": "Acme Corp",
                        "slots": {},
                    },
                },
                "steps": [
                    {
                        "step_id": "lt1",
                        "type": "ledger_transaction",
                        "description": "Pay from {buyer_name} to {seller_name}",
                        "ledger_entries": [
                            {
                                "amount": 100,
                                "direction": "debit",
                                "ledger_account_id": "$ref:ledger_account.cash",
                            },
                            {
                                "amount": 100,
                                "direction": "credit",
                                "ledger_account_id": "$ref:ledger_account.rev",
                            },
                        ],
                    },
                ],
            }
        ],
    }


def _actor_flow_session() -> tuple[str, SessionState]:
    config = DataLoaderConfig.model_validate(_make_actor_flow_config())
    flow_irs = compile_flows(config.funds_flows, config)
    token = "test-actor-config-token"
    recipe = GenerationRecipeV1(flow_ref="actor_test", instances=1, seed=42).model_dump()
    sess = SessionState(
        session_token=token,
        api_key="k",
        org_id="o",
        config=config,
        config_json_text=config.model_dump_json(),
        registry=RefRegistry(),
        batches=[],
        base_config_json=config.model_dump_json(),
        working_config_json=config.model_dump_json(),
        pattern_flow_ir=flow_irs,
        pattern_expanded_flows=list(config.funds_flows),
        generation_recipes={"actor_test": recipe},
    )
    return token, sess


def test_actor_config_get_unauthorized():
    client = TestClient(app)
    r = client.get("/api/flows/0/actor-config?session_token=bad&frame=buyer")
    assert r.status_code == 401


def test_actor_config_get_ok():
    token, sess = _actor_flow_session()
    sessions[token] = sess
    try:
        client = TestClient(app)
        r = client.get(
            f"/api/flows/0/actor-config?session_token={token}&frame=buyer",
        )
        assert r.status_code == 200
        assert "flow-actor-config-root" in r.text
        assert "Actor" in r.text
        assert "buyer" in r.text
        assert "actor-entity-type" in r.text
    finally:
        sessions.pop(token, None)


def test_actor_config_post_sets_dataset(monkeypatch):
    """Merge + compose; stub dry_run/preview — minimal actor_test config lacks full DAG."""
    monkeypatch.setattr("dataloader.flows_mutation.dry_run", lambda *args, **kwargs: [])
    monkeypatch.setattr("dataloader.flows_mutation.build_preview", lambda *args, **kwargs: [])

    token, sess = _actor_flow_session()
    sessions[token] = sess
    try:
        client = TestClient(app)
        r = client.post(
            "/api/flows/0/actor-config",
            json={"frame": "buyer", "override": {"dataset": "harry_potter"}},
            headers={"X-Session-Token": token},
        )
        assert r.status_code == 200
        assert r.json().get("status") == "ok"
        ao = sessions[token].generation_recipes["actor_test"]["actor_overrides"]
        assert ao["buyer"]["dataset"] == "harry_potter"
    finally:
        sessions.pop(token, None)


def test_actor_config_post_unknown_frame():
    token, sess = _actor_flow_session()
    sessions[token] = sess
    try:
        client = TestClient(app)
        r = client.post(
            "/api/flows/0/actor-config",
            json={"frame": "nobody", "override": {}},
            headers={"X-Session-Token": token},
        )
        assert r.status_code == 404
    finally:
        sessions.pop(token, None)
