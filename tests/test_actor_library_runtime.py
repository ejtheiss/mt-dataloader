"""Plan 11a Phase 0 — actor library hydrate + materialize."""

from __future__ import annotations

from types import SimpleNamespace

from dataloader.actor_library_runtime import (
    materialize_actor_bindings_to_generation_recipes,
    recipe_flow_ref,
)
from dataloader.engine import RefRegistry
from dataloader.flows_mutation import prepare_actor_library_for_compose
from dataloader.session import SessionState
from dataloader.session.draft_persist import (
    loader_draft_from_session,
    merge_loader_draft_into_session,
)
from models import DataLoaderConfig
from tests.paths import EXAMPLES_DIR


def test_recipe_flow_ref_strips_instance_suffix() -> None:
    assert recipe_flow_ref("wire__0042") == "wire"
    assert recipe_flow_ref("plain") == "plain"


def test_materialize_applies_library_rows_to_actor_overrides() -> None:
    sess = SimpleNamespace(
        actor_bindings={"pat": {"user_1": "L1"}},
        actor_library=[
            {
                "library_actor_id": "L1",
                "label": "VIP",
                "frame_type": "user",
                "dataset": "vip",
                "entity_type": "individual",
            }
        ],
        generation_recipes={
            "pat": {
                "version": "v1",
                "flow_ref": "pat",
                "instances": 1,
                "actor_overrides": {"user_1": {}},
            }
        },
    )
    materialize_actor_bindings_to_generation_recipes(sess)
    ov = sess.generation_recipes["pat"]["actor_overrides"]["user_1"]
    assert ov["dataset"] == "vip"
    assert ov["entity_type"] == "individual"


def test_loader_draft_round_trips_actor_fields() -> None:
    cfg = DataLoaderConfig()
    s = SessionState(
        session_token="t",
        api_key="k",
        org_id="o",
        config=cfg,
        config_json_text="{}",
        registry=RefRegistry(),
        batches=[],
        actor_library=[
            {
                "library_actor_id": "a1",
                "label": "Actor A",
                "frame_type": "direct",
                "customer_name": "Co",
            }
        ],
        actor_bindings={"flow_x": {"frame1": "a1"}},
    )
    d = loader_draft_from_session(s)
    assert len(d.actor_library) == 1
    assert d.actor_library[0].library_actor_id == "a1"
    assert d.actor_bindings["flow_x"]["frame1"] == "a1"

    s2 = SessionState(
        session_token="t2",
        api_key="k2",
        org_id="o",
        config=cfg,
        config_json_text="{}",
        registry=RefRegistry(),
        batches=[],
    )
    merge_loader_draft_into_session(s2, d)
    assert s2.actor_bindings == {"flow_x": {"frame1": "a1"}}
    assert s2.actor_library[0]["library_actor_id"] == "a1"


def test_prepare_hydrates_from_demo_authoring() -> None:
    raw = (EXAMPLES_DIR / "funds_flow_demo.json").read_text()
    cfg = DataLoaderConfig.model_validate_json(raw)
    fc = cfg.funds_flows[0]
    rk = recipe_flow_ref(fc.ref)
    first_alias = next(iter(fc.actors.keys()))
    sess = SessionState(
        session_token="t",
        api_key="k",
        org_id="o",
        config=cfg,
        config_json_text="{}",
        registry=RefRegistry(),
        batches=[],
        authoring_config_json=raw,
        generation_recipes={
            rk: {
                "version": "v1",
                "flow_ref": rk,
                "instances": 2,
                "seed": 1,
                "actor_overrides": {},
            }
        },
    )
    prepare_actor_library_for_compose(sess)
    assert sess.actor_bindings.get(rk, {}).get(first_alias)
    lib_ids = {row["library_actor_id"] for row in sess.actor_library}
    assert any(x.startswith(f"legacy:{rk}:") for x in lib_ids)
