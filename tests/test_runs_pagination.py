"""Unit tests for :mod:`dataloader.runs_pagination` cursors."""

from __future__ import annotations

import pytest

from dataloader.runs_pagination import (
    RunsSeekCursorError,
    decode_runs_seek_cursor,
    encode_runs_seek_cursor,
)


def test_seek_cursor_roundtrip() -> None:
    sa = "2026-04-10T12:00:00+00:00"
    rid = "run_abc_01"
    tok = encode_runs_seek_cursor(sa, rid)
    assert decode_runs_seek_cursor(tok) == (sa, rid)


@pytest.mark.parametrize(
    "bad",
    ["", "!!!", "e30=", '{"v":2,"sa":"a","rid":"b"}'],
)
def test_seek_cursor_rejects_invalid(bad: str) -> None:
    with pytest.raises(RunsSeekCursorError):
        decode_runs_seek_cursor(bad)
