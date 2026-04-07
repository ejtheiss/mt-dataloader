"""Fund Flows display: prefer generated IR when recipes exist (pattern vs scaled)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dataloader.routers.flows import _display_flow_session_sources, _recipe_flow_ref


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("tradeify__0042", "tradeify"),
        ("tradeify", "tradeify"),
        ("my_flow__0000", "my_flow"),
        ("no_suffix__x", "no_suffix__x"),
    ],
)
def test_recipe_flow_ref(ref: str, expected: str) -> None:
    assert _recipe_flow_ref(ref) == expected


def test_display_prefers_generated_when_recipes_and_flow_ir() -> None:
    pattern_ir = [object()]
    gen_ir = [object(), object()]
    sess = SimpleNamespace(
        pattern_flow_ir=pattern_ir,
        pattern_expanded_flows=["p"],
        flow_ir=gen_ir,
        expanded_flows=["a", "b"],
        generation_recipes={"x": {}},
    )
    ir, exp = _display_flow_session_sources(sess)
    assert ir is gen_ir
    assert exp == ["a", "b"]


def test_display_falls_back_to_pattern_without_recipes() -> None:
    pattern_ir = [object()]
    gen_ir = [object()]
    sess = SimpleNamespace(
        pattern_flow_ir=pattern_ir,
        pattern_expanded_flows=["p"],
        flow_ir=gen_ir,
        expanded_flows=["g"],
        generation_recipes={},
    )
    ir, exp = _display_flow_session_sources(sess)
    assert ir is pattern_ir
    assert exp == ["p"]


def test_display_falls_back_when_recipes_but_empty_flow_ir() -> None:
    pattern_ir = [object()]
    sess = SimpleNamespace(
        pattern_flow_ir=pattern_ir,
        pattern_expanded_flows=["p"],
        flow_ir=[],
        expanded_flows=[],
        generation_recipes={"x": {}},
    )
    ir, exp = _display_flow_session_sources(sess)
    assert ir is pattern_ir
    assert exp == ["p"]
