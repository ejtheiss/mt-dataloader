"""Tests for repo-root ``jsonutil`` helpers."""

from __future__ import annotations

import json

from jsonutil import dumps_jsonl_record, dumps_pretty, loads_str


def test_dumps_jsonl_record_one_line_and_roundtrip_dict():
    line = dumps_jsonl_record({"a": 1, "b": "x"})
    assert line.endswith("\n")
    assert "\n" not in line[:-1]
    obj = loads_str(line.strip())
    assert obj == {"a": 1, "b": "x"}


def test_dumps_jsonl_record_non_json_native_uses_str():
    sentinel = object()
    line = dumps_jsonl_record({"x": sentinel})
    obj = json.loads(line.strip())
    assert obj["x"] == str(sentinel)


def test_dumps_pretty_multiline():
    s = dumps_pretty({"k": [1, 2]})
    assert "\n" in s
    assert loads_str(s) == {"k": [1, 2]}
