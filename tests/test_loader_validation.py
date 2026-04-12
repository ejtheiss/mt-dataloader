"""Unit tests for ``dataloader.loader_validation`` (plan 04 pipeline compose)."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from dataloader.loader_validation import (
    LoaderValidationFailure,
    loader_validation_failure_htmx_parts,
    loader_validation_failure_to_envelope,
    parse_loader_config_bytes,
    parse_loader_config_json_text,
    require_pydantic_obj,
    run_headless_validate_json,
    try_parse_pydantic_json_bytes,
    try_parse_pydantic_obj,
)
from models import DataLoaderConfig, GenerationRecipeV1
from models.loader_setup_json import LoaderSetupErrorItem

_MINIMAL = Path(__file__).resolve().parent.parent / "examples" / "psp_minimal.json"
_EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


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


def test_examples_json_parse_includes_funds_flow_display_fields():
    """Plan 10c: ``FundsFlowConfig`` must accept ``display_title`` / ``display_summary`` (examples use them)."""
    paths = sorted(_EXAMPLES_DIR.glob("*.json"))
    assert paths, f"no examples under {_EXAMPLES_DIR}"
    for path in paths:
        pr = parse_loader_config_bytes(path.read_bytes())
        assert pr.body_invalid is None, path.name
        assert pr.error is None, f"{path.name}: {pr.error}"
        assert pr.config is not None


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


def test_loader_validation_failure_to_envelope_includes_flow_diagnostics():
    diag = {
        "rule_id": "test_rule",
        "severity": "warning",
        "step_id": "s1",
        "account_id": None,
        "message": "advisory",
    }
    failure = LoaderValidationFailure(
        message="DAG\noops",
        v1_phase="dag",
        v1_errors=(LoaderSetupErrorItem(code="cycle_error", message="bad", path="(dag)"),),
        v1_flow_diagnostic_dicts=(diag,),
    )
    env = loader_validation_failure_to_envelope(failure)
    assert env.ok is False
    assert env.phase == "dag"
    assert len(env.diagnostics) == 1
    assert env.diagnostics[0].rule_id == "test_rule"
    assert env.diagnostics[0].severity == "warning"


def test_loader_validation_failure_htmx_parts_uses_v1_errors():
    failure = LoaderValidationFailure(
        message="ignored for title when v1_errors set",
        v1_phase="parse",
        v1_errors=(
            LoaderSetupErrorItem(code="missing", message="field required", path="connections"),
        ),
    )
    title, detail = loader_validation_failure_htmx_parts(failure)
    assert "field required" in title
    assert "Phase: parse" in detail
    assert "Primary error path: connections" in detail


def test_loader_validation_failure_htmx_parts_fallback_message():
    failure = LoaderValidationFailure(message="Title line\nDetail line")
    title, detail = loader_validation_failure_htmx_parts(failure)
    assert title == "Title line"
    assert detail == "Detail line"
