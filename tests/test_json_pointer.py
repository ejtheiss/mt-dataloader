"""RFC 6901 JSON Pointer set helper (Plan 05 patch-json)."""

import pytest

from dataloader.json_pointer import apply_json_pointer_set


def test_set_nested_dict_creates_parents():
    doc: dict = {"a": 1}
    apply_json_pointer_set(doc, "/b/c", 2)
    assert doc == {"a": 1, "b": {"c": 2}}


def test_set_list_index_and_append():
    doc: dict = {"items": [{"x": 0}, {"x": 1}]}
    apply_json_pointer_set(doc, "/items/0/x", 9)
    assert doc["items"][0]["x"] == 9
    apply_json_pointer_set(doc, "/items/2", {"x": 2})
    assert len(doc["items"]) == 3
    assert doc["items"][2] == {"x": 2}


def test_rejects_root_pointer():
    with pytest.raises(ValueError, match="root"):
        apply_json_pointer_set({}, "", "x")


def test_rejects_invalid_pointer():
    with pytest.raises(ValueError, match="start"):
        apply_json_pointer_set({}, "no_slash", 1)
