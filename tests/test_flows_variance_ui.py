"""Unit tests for Fund Flows scenario-builder variance row context (server-side)."""

from routers.flows import _step_variance_ui_fields


def test_variance_ui_absent_step_follows_global():
    assert _step_variance_ui_fields("deposit", None) == {
        "variance_mode": "global",
        "variance_custom_min": 0.0,
        "variance_custom_max": 0.0,
    }
    assert _step_variance_ui_fields("deposit", {})["variance_mode"] == "global"
    assert _step_variance_ui_fields("deposit", {"step_variance": {}})["variance_mode"] == "global"
    assert _step_variance_ui_fields("deposit", {"step_variance": {"other": {}}})[
        "variance_mode"
    ] == "global"


def test_variance_ui_empty_dict_is_locked():
    r = _step_variance_ui_fields("deposit", {"step_variance": {"deposit": {}}})
    assert r["variance_mode"] == "locked"


def test_variance_ui_non_empty_dict_is_custom():
    r = _step_variance_ui_fields(
        "deposit",
        {"step_variance": {"deposit": {"min_pct": -5.0, "max_pct": 12.5}}},
    )
    assert r["variance_mode"] == "custom"
    assert r["variance_custom_min"] == -5.0
    assert r["variance_custom_max"] == 12.5
