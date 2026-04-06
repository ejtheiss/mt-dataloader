"""Unit tests for MT webhook endpoint HTTP helpers."""

from __future__ import annotations

import dataloader.mt_webhook_endpoints as mwe


def test_normalize_list_payload_array():
    assert mwe._normalize_list_payload([{"id": "1"}, {"id": "2"}]) == [
        {"id": "1"},
        {"id": "2"},
    ]


def test_normalize_list_payload_items():
    data = {"items": [{"id": "a"}], "after_cursor": None}
    assert mwe._normalize_list_payload(data) == [{"id": "a"}]


def test_normalize_list_payload_empty():
    assert mwe._normalize_list_payload({}) == []
    assert mwe._normalize_list_payload("bad") == []


def test_normalize_webhook_url():
    assert mwe.normalize_webhook_url(" https://x.com/path/ ") == "https://x.com/path"


def test_analyze_org_webhook_listeners_match_and_stale():
    expected = "https://abc.ngrok.app/webhooks/mt"
    out = mwe.analyze_org_webhook_listeners(
        [
            {"id": "whe_1", "url": "https://old.ngrok.app/webhooks/mt"},
            {"id": "whe_2", "url": "https://abc.ngrok.app/webhooks/mt/"},
        ],
        expected,
    )
    assert out["match"] is True
    assert out["endpoint_id"] == "whe_2"
    assert out["stale_url"] is None


def test_analyze_org_webhook_listeners_stale_no_exact():
    expected = "https://new.ngrok.app/webhooks/mt"
    out = mwe.analyze_org_webhook_listeners(
        [{"id": "whe_1", "url": "https://old.ngrok.app/webhooks/mt"}],
        expected,
    )
    assert out["match"] is False
    assert out["endpoint_id"] is None
    assert out["stale_url"] == "https://old.ngrok.app/webhooks/mt"


def test_analyze_org_webhook_listeners_no_path():
    out = mwe.analyze_org_webhook_listeners(
        [{"id": "whe_1", "url": "https://x.com/other-hook"}],
        "https://x.com/webhooks/mt",
    )
    assert out["match"] is False
    assert out["stale_url"] is None
