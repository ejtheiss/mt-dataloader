"""Fund Flow trace vs per-step metadata (UI / save semantics)."""

from __future__ import annotations

from dataloader.flow_trace_metadata import forbidden_trace_keys, step_only_metadata


def test_step_only_metadata_excludes_flow_trace_keys() -> None:
    flow_trace = {"transfer_id": "xfer-internal_transfer-0", "env": "sandbox"}
    payload = {
        "metadata": {
            "transfer_id": "dup-should-hide",
            "role": "book_side",
            "_flow_optional_group": "og1",
        }
    }
    assert step_only_metadata(flow_trace, payload) == {"role": "book_side"}


def test_step_only_metadata_empty_payload() -> None:
    assert step_only_metadata({"a": "1"}, {}) == {}


def test_forbidden_trace_keys() -> None:
    assert forbidden_trace_keys("deal_id", {"region": "us"}) == {"deal_id", "region"}
