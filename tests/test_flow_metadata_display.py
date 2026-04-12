"""Plan 10c: metadata index resolution for list row → working ``funds_flows`` index."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dataloader.routers.flows.helpers import (
    get_funds_flow_display_fields_for_display_row,
    resolve_working_funds_flow_index_for_metadata,
)


def _session(
    *,
    working_json: str,
    recipes: dict | None = None,
    flow_ir: list | None = None,
    expanded_flows: list | None = None,
    pattern_ir: list | None = None,
    pattern_exp: list | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        working_config_json=working_json,
        config_json_text=None,
        generation_recipes=recipes or {},
        flow_ir=flow_ir or [],
        pattern_flow_ir=pattern_ir or [],
        pattern_expanded_flows=pattern_exp or [],
        expanded_flows=expanded_flows or [],
    )


def test_resolve_fallback_direct_index_when_no_expanded_sources():
    """Smoke-style sessions: no recipes/IR expansion → display index == funds_flows index."""
    sess = _session(
        working_json='{"funds_flows": [{"ref": "a"}, {"ref": "b"}]}',
    )
    assert resolve_working_funds_flow_index_for_metadata(sess, 0) == 0
    assert resolve_working_funds_flow_index_for_metadata(sess, 1) == 1


def test_resolve_maps_instance_ref_to_pattern_row():
    sess = _session(
        working_json='{"funds_flows": [{"ref": "wire_pattern"}]}',
        recipes={"any": True},
        flow_ir=[1],
        expanded_flows=[SimpleNamespace(ref="wire_pattern__0003")],
    )
    assert resolve_working_funds_flow_index_for_metadata(sess, 0) == 0


def test_resolve_same_pattern_two_display_rows():
    sess = _session(
        working_json='{"funds_flows": [{"ref": "p"}]}',
        recipes={"any": True},
        flow_ir=[1, 2],
        expanded_flows=[
            SimpleNamespace(ref="p__0000"),
            SimpleNamespace(ref="p__0001"),
        ],
    )
    assert resolve_working_funds_flow_index_for_metadata(sess, 0) == 0
    assert resolve_working_funds_flow_index_for_metadata(sess, 1) == 0


def test_resolve_falls_back_to_instance_ref_in_funds_flows():
    sess = _session(
        working_json='{"funds_flows": [{"ref": "only_instance__1"}]}',
        recipes={"any": True},
        flow_ir=[1],
        expanded_flows=[SimpleNamespace(ref="only_instance__1")],
    )
    assert resolve_working_funds_flow_index_for_metadata(sess, 0) == 0


def test_resolve_empty_working_config_raises():
    sess = SimpleNamespace(
        working_config_json="",
        config_json_text="",
        generation_recipes={},
        flow_ir=[],
        pattern_flow_ir=[],
        pattern_expanded_flows=[],
        expanded_flows=[],
    )
    with pytest.raises(ValueError, match="Session has no working config JSON"):
        resolve_working_funds_flow_index_for_metadata(sess, 0)


def test_resolve_invalid_json_raises():
    sess = _session(working_json="{not json")
    with pytest.raises(ValueError, match="Invalid working config JSON"):
        resolve_working_funds_flow_index_for_metadata(sess, 0)


def test_resolve_expanded_row_missing_ref_raises():
    sess = _session(
        working_json='{"funds_flows": [{"ref": "p"}]}',
        recipes={"any": True},
        flow_ir=[1],
        expanded_flows=[SimpleNamespace()],
    )
    with pytest.raises(ValueError, match="has no ref"):
        resolve_working_funds_flow_index_for_metadata(sess, 0)


def test_resolve_no_matching_flow_raises():
    sess = _session(
        working_json='{"funds_flows": [{"ref": "other"}]}',
        recipes={"any": True},
        flow_ir=[1],
        expanded_flows=[SimpleNamespace(ref="missing__1")],
    )
    with pytest.raises(ValueError, match="No funds_flows entry"):
        resolve_working_funds_flow_index_for_metadata(sess, 0)


def test_display_fields_prefer_working_json_over_expanded():
    """List row titles follow ``working_config_json`` so POST /metadata edits show without re-compile."""
    expanded = SimpleNamespace(
        display_title="From IR",
        display_summary="IR summary",
    )
    sess = _session(
        working_json=(
            '{"funds_flows": [{"ref": "p", "display_title": " Authoritative ", '
            '"display_summary": "  Summary text  "}]}'
        ),
        recipes={"any": True},
        flow_ir=[1],
        expanded_flows=[SimpleNamespace(ref="p__0000")],
    )
    t, s = get_funds_flow_display_fields_for_display_row(sess, 0, expanded)
    assert t == "Authoritative"
    assert s == "Summary text"


def test_display_fields_fallback_when_json_keys_absent():
    sess = _session(
        working_json='{"funds_flows": [{"ref": "p"}]}',
        recipes={"any": True},
        flow_ir=[1],
        expanded_flows=[SimpleNamespace(ref="p__0000", display_title="Compiled")],
    )
    t, s = get_funds_flow_display_fields_for_display_row(sess, 0, sess.expanded_flows[0])
    assert t == "Compiled"
    assert s is None


def test_display_fields_same_pattern_two_rows_share_working_row():
    sess = _session(
        working_json=('{"funds_flows": [{"ref": "p", "display_title": "One pattern title"}]}'),
        recipes={"any": True},
        flow_ir=[1, 2],
        expanded_flows=[
            SimpleNamespace(ref="p__0000", display_title="Stale0"),
            SimpleNamespace(ref="p__0001", display_title="Stale1"),
        ],
    )
    t0, _ = get_funds_flow_display_fields_for_display_row(sess, 0, sess.expanded_flows[0])
    t1, _ = get_funds_flow_display_fields_for_display_row(sess, 1, sess.expanded_flows[1])
    assert t0 == t1 == "One pattern title"
