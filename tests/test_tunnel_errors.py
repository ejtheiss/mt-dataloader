"""Ngrok start error interpretation (quota / agent limits)."""

from __future__ import annotations

import dataloader.tunnel as t


def test_interpret_err_ngrok_108():
    raw = "authentication failed: ... ERR_NGROK_108 ..."
    info = t.interpret_ngrok_start_error(ValueError(raw))
    assert info["code"] == "ERR_NGROK_108"
    assert info["hint"] is not None
    assert "3 concurrent" in info["hint"].lower() or "3 concurrent agent" in info["hint"].lower()


def test_interpret_three_simultaneous_phrase():
    raw = "Your account is limited to 3 simultaneous ngrok agent sessions."
    info = t.interpret_ngrok_start_error(RuntimeError(raw))
    assert info["code"] == "ERR_NGROK_108"


def test_interpret_unknown():
    info = t.interpret_ngrok_start_error(ConnectionError("reset by peer"))
    assert info["code"] is None
    assert info["hint"] is None
    assert "reset" in info["raw"]
