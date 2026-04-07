"""Unit tests for ``dataloader.loader_validation`` (plan 04 pipeline compose)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from dataloader.loader_validation import (
    parse_loader_config_json_text,
    require_pydantic_obj,
    run_headless_validate_json,
    try_parse_pydantic_json_bytes,
    try_parse_pydantic_obj,
)
from models import DataLoaderConfig, GenerationRecipeV1

_MINIMAL = Path(__file__).resolve().parent.parent / "examples" / "psp_minimal.json"


@pytest.fixture
def psp_minimal_bytes() -> bytes:
    assert _MINIMAL.is_file(), f"missing {_MINIMAL}"
    return _MINIMAL.read_bytes()


def test_run_headless_validate_json_success(psp_minimal_bytes: bytes):
    out = run_headless_validate_json(psp_minimal_bytes)
    assert out.ok is True
    assert out.phase == "complete"
    assert out.errors == []
    assert out.data["has_funds_flows"] is True
    assert out.data["resource_count"] >= 1
    assert out.data["batch_count"] >= 1


def test_run_headless_validate_json_parse_phase():
    out = run_headless_validate_json(b'{"connections": "not_a_list"}')
    assert out.ok is False
    assert out.phase == "parse"
    assert out.errors
    assert out.errors[0].code


def test_parse_loader_config_json_text_roundtrip(psp_minimal_bytes: bytes):
    text = psp_minimal_bytes.decode("utf-8")
    pr = parse_loader_config_json_text(text)
    assert pr.error is None
    assert pr.config is not None
    assert pr.config.connections  # minimal fixture has connections


def test_try_parse_pydantic_json_bytes_invalid():
    recipe, err = try_parse_pydantic_json_bytes(GenerationRecipeV1, b"not json")
    assert recipe is None
    assert err is not None


def test_require_pydantic_obj_raises():
    with pytest.raises(ValidationError):
        require_pydantic_obj(DataLoaderConfig, {"connections": "bad"})


def test_try_parse_pydantic_obj_ok():
    cfg, err = try_parse_pydantic_obj(DataLoaderConfig, {})
    assert err is None
    assert cfg is not None
