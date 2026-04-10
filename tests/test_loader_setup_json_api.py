"""JSON API v1 envelope for validate-json, config/save, revalidate-json (plan 04)."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from dataloader.engine import RefRegistry
from dataloader.loader_validation import LoaderValidationFailure, LoaderValidationSuccess
from dataloader.main import app
from dataloader.session import SessionState, sessions
from models import DataLoaderConfig
from models.loader_setup_json import LoaderSetupErrorItem

_MINIMAL = Path(__file__).resolve().parent.parent / "examples" / "psp_minimal.json"


@pytest.fixture
def psp_minimal_bytes() -> bytes:
    assert _MINIMAL.is_file(), f"missing {_MINIMAL}"
    return _MINIMAL.read_bytes()


def test_validate_json_v1_success_envelope(psp_minimal_bytes: bytes):
    with TestClient(app) as client:
        r = client.post("/api/validate-json", content=psp_minimal_bytes)
    assert r.status_code == 200
    data = r.json()
    assert data["schema_version"] == 1
    assert data["ok"] is True
    assert data["phase"] == "complete"
    assert data["errors"] == []
    assert data["warnings"] == []
    assert data["diagnostics"] == []
    assert "resource_count" in data["data"]
    assert "batch_count" in data["data"]
    assert "has_funds_flows" in data["data"]
    assert isinstance(data["data"]["resource_count"], int)
    assert data["data"]["has_funds_flows"] is True


def test_validate_json_v1_invalid_body_422():
    with TestClient(app) as client:
        r = client.post("/api/validate-json", content=b"not json")
    assert r.status_code == 422
    data = r.json()
    assert data["schema_version"] == 1
    assert data["ok"] is False
    assert len(data["errors"]) == 1
    assert data["errors"][0]["code"] == "invalid_body"


def test_validate_json_v1_parse_errors_use_code_not_type():
    # `{}` is a valid (empty) DataLoaderConfig; use a schema violation instead.
    with TestClient(app) as client:
        r = client.post(
            "/api/validate-json",
            content=b'{"connections": "must_be_a_list"}',
        )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is False
    assert data["phase"] == "parse"
    assert data["errors"]
    assert "code" in data["errors"][0]
    assert "type" not in data["errors"][0]


def test_config_save_v1_unknown_session_404():
    with TestClient(app) as client:
        r = client.post(
            "/api/config/save",
            json={"session_token": "definitely-not-a-session", "config_json": "{}"},
        )
    assert r.status_code == 404
    data = r.json()
    assert data["schema_version"] == 1
    assert data["ok"] is False
    assert data["errors"][0]["code"] == "session_expired"


def test_config_save_v1_inner_config_json_invalid_json_422():
    token = "save-inner-json-test"
    cfg = DataLoaderConfig.model_validate({})
    sessions[token] = SessionState(
        session_token=token,
        api_key="k",
        org_id="o",
        config=cfg,
        config_json_text="{}",
        registry=RefRegistry(),
        batches=[[]],
        working_config_json="{}",
    )
    try:
        with TestClient(app) as client:
            r = client.post(
                "/api/config/save",
                json={"session_token": token, "config_json": "not json"},
            )
        assert r.status_code == 422
        data = r.json()
        assert data["ok"] is False
        assert data["errors"]
        assert data["errors"][0]["code"] == "invalid_body"
    finally:
        sessions.pop(token, None)


def test_config_save_v1_inner_config_json_schema_422():
    token = "save-inner-schema-test"
    cfg = DataLoaderConfig.model_validate({})
    sessions[token] = SessionState(
        session_token=token,
        api_key="k",
        org_id="o",
        config=cfg,
        config_json_text="{}",
        registry=RefRegistry(),
        batches=[[]],
        working_config_json="{}",
    )
    try:
        with TestClient(app) as client:
            r = client.post(
                "/api/config/save",
                json={
                    "session_token": token,
                    "config_json": '{"connections": "not_a_list"}',
                },
            )
        assert r.status_code == 422
        data = r.json()
        assert data["ok"] is False
        assert data["phase"] == "parse"
        assert data["errors"]
        assert "code" in data["errors"][0]
    finally:
        sessions.pop(token, None)


def test_revalidate_json_v1_unknown_session_404():
    with TestClient(app) as client:
        r = client.post(
            "/api/revalidate-json",
            json={
                "session_token": "no-such-session",
                "config_json": "{}",
            },
        )
    assert r.status_code == 404
    data = r.json()
    assert data["schema_version"] == 1
    assert data["ok"] is False
    assert data["errors"][0]["code"] == "session_expired"


def test_revalidate_json_v1_invalid_body_422():
    with TestClient(app) as client:
        r = client.post("/api/revalidate-json", content=b"not json")
    assert r.status_code == 422
    data = r.json()
    assert data["ok"] is False
    assert data["errors"][0]["code"] == "invalid_body"


@patch("dataloader.routers.setup.validation_funnel.persist_loader_draft", new_callable=AsyncMock)
@patch(
    "dataloader.routers.setup.validation_funnel.run_loader_validation_pipeline",
    new_callable=AsyncMock,
)
def test_revalidate_json_v1_success_rotates_token(mock_pipe, _mock_persist):
    token = "reval-ok-token"
    new_tok: str | None = None
    cfg = DataLoaderConfig.model_validate({})
    sessions[token] = SessionState(
        session_token=token,
        api_key="k",
        org_id="o",
        config=cfg,
        config_json_text="{}",
        registry=RefRegistry(),
        batches=[[]],
        working_config_json="{}",
    )
    mock_pipe.return_value = LoaderValidationSuccess(
        config=cfg,
        config_json_text="{}",
        authoring_config_json="{}",
        flow_irs=[],
        expanded_flows=[],
        mermaid_diagrams=None,
        view_data_cache=None,
        discovery=None,
        org_registry=None,
        reconciliation=None,
        registry=RefRegistry(),
        skip_refs=set(),
        batches=[[]],
        preview_items=[],
        flow_diagnostics=[],
    )
    try:
        with TestClient(app) as client:
            r = client.post(
                "/api/revalidate-json",
                json={"session_token": token, "config_json": "{}"},
            )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["phase"] == "complete"
        assert "session_token" in data["data"]
        new_tok = data["data"]["session_token"]
        assert new_tok != token
        assert new_tok in sessions
        assert token not in sessions
    finally:
        sessions.pop(token, None)
        if new_tok:
            sessions.pop(new_tok, None)


@patch(
    "dataloader.routers.setup.validation_funnel.run_loader_validation_pipeline",
    new_callable=AsyncMock,
)
def test_revalidate_json_v1_failure_includes_diagnostics(mock_pipe):
    token = "reval-fail-diag-token"
    cfg = DataLoaderConfig.model_validate({})
    sessions[token] = SessionState(
        session_token=token,
        api_key="k",
        org_id="o",
        config=cfg,
        config_json_text="{}",
        registry=RefRegistry(),
        batches=[[]],
        working_config_json="{}",
    )
    mock_pipe.return_value = LoaderValidationFailure(
        message="Can't build execution plan\nx",
        v1_phase="dag",
        v1_errors=(LoaderSetupErrorItem(code="staged_dependency", message="hint", path="(dag)"),),
        v1_flow_diagnostic_dicts=(
            {
                "rule_id": "flow_rule_x",
                "severity": "info",
                "step_id": "a",
                "account_id": None,
                "message": "note",
            },
        ),
    )
    try:
        with TestClient(app) as client:
            r = client.post(
                "/api/revalidate-json",
                json={"session_token": token, "config_json": "{}"},
            )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is False
        assert data["phase"] == "dag"
        assert len(data["diagnostics"]) == 1
        assert data["diagnostics"][0]["rule_id"] == "flow_rule_x"
        assert data["diagnostics"][0]["severity"] == "info"
    finally:
        sessions.pop(token, None)


def test_patch_json_v1_unknown_session_404():
    with TestClient(app) as client:
        r = client.post(
            "/api/config/patch-json",
            json={"session_token": "no-such-session", "shallow_merge": {}},
        )
    assert r.status_code == 404
    data = r.json()
    assert data["schema_version"] == 1
    assert data["ok"] is False
    assert data["errors"][0]["code"] == "session_expired"


def test_patch_json_v1_extra_fields_422():
    with TestClient(app) as client:
        r = client.post(
            "/api/config/patch-json",
            json={
                "session_token": "x",
                "shallow_merge": {},
                "unknown_field": 1,
            },
        )
    assert r.status_code == 422
    data = r.json()
    assert data["ok"] is False
    assert data["errors"]


@patch("dataloader.routers.setup.validation_funnel.persist_loader_draft", new_callable=AsyncMock)
@patch(
    "dataloader.routers.setup.validation_funnel.run_loader_validation_pipeline",
    new_callable=AsyncMock,
)
def test_patch_json_v1_merges_before_pipeline(mock_pipe, _mock_persist):
    token = "patch-json-merge-token"
    new_tok: str | None = None
    cfg = DataLoaderConfig.model_validate({"customer_name": "before"})
    sessions[token] = SessionState(
        session_token=token,
        api_key="k",
        org_id="o",
        config=cfg,
        config_json_text='{"customer_name": "before"}',
        registry=RefRegistry(),
        batches=[[]],
        working_config_json='{"customer_name": "before"}',
    )
    out_cfg = DataLoaderConfig.model_validate({"customer_name": "after"})

    async def capture_pipe(raw_json: bytes, *_a, **_kw):
        obj = json.loads(raw_json.decode())
        assert obj.get("customer_name") == "after"
        assert obj.get("connections") == []
        return LoaderValidationSuccess(
            config=out_cfg,
            config_json_text='{"customer_name": "after"}',
            authoring_config_json="{}",
            flow_irs=[],
            expanded_flows=[],
            mermaid_diagrams=None,
            view_data_cache=None,
            discovery=None,
            org_registry=None,
            reconciliation=None,
            registry=RefRegistry(),
            skip_refs=set(),
            batches=[[]],
            preview_items=[],
            flow_diagnostics=[],
        )

    mock_pipe.side_effect = capture_pipe
    try:
        with TestClient(app) as client:
            r = client.post(
                "/api/config/patch-json",
                json={
                    "session_token": token,
                    "shallow_merge": {"customer_name": "after", "connections": []},
                },
            )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        new_tok = data["data"]["session_token"]
        assert mock_pipe.await_count == 1
    finally:
        sessions.pop(token, None)
        if new_tok:
            sessions.pop(new_tok, None)


@patch("dataloader.routers.setup.validation_funnel.persist_loader_draft", new_callable=AsyncMock)
@patch(
    "dataloader.routers.setup.validation_funnel.run_loader_validation_pipeline",
    new_callable=AsyncMock,
)
def test_patch_json_v1_pointer_sets_after_shallow_merge(mock_pipe, _mock_persist):
    token = "patch-json-pointer-token"
    new_tok: str | None = None
    cfg = DataLoaderConfig.model_validate({"customer_name": "acme", "connections": []})
    sessions[token] = SessionState(
        session_token=token,
        api_key="k",
        org_id="o",
        config=cfg,
        config_json_text='{"customer_name": "before", "connections": []}',
        registry=RefRegistry(),
        batches=[[]],
        working_config_json='{"customer_name": "before", "connections": []}',
    )
    out_cfg = DataLoaderConfig.model_validate({"customer_name": "patched", "connections": []})

    async def capture_pipe(raw_json: bytes, *_a, **_kw):
        obj = json.loads(raw_json.decode())
        assert obj.get("customer_name") == "patched"
        assert obj.get("connections") == []
        return LoaderValidationSuccess(
            config=out_cfg,
            config_json_text="{}",
            authoring_config_json="{}",
            flow_irs=[],
            expanded_flows=[],
            mermaid_diagrams=None,
            view_data_cache=None,
            discovery=None,
            org_registry=None,
            reconciliation=None,
            registry=RefRegistry(),
            skip_refs=set(),
            batches=[[]],
            preview_items=[],
            flow_diagnostics=[],
        )

    mock_pipe.side_effect = capture_pipe
    try:
        with TestClient(app) as client:
            r = client.post(
                "/api/config/patch-json",
                json={
                    "session_token": token,
                    "shallow_merge": {"connections": []},
                    "pointer_sets": [{"path": "/customer_name", "value": "patched"}],
                },
            )
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        new_tok = data["data"]["session_token"]
        assert mock_pipe.await_count == 1
    finally:
        sessions.pop(token, None)
        if new_tok:
            sessions.pop(new_tok, None)


def test_patch_json_v1_pointer_set_invalid_422():
    token = "patch-json-bad-pointer"
    cfg = DataLoaderConfig.model_validate({})
    sessions[token] = SessionState(
        session_token=token,
        api_key="k",
        org_id="o",
        config=cfg,
        config_json_text="{}",
        registry=RefRegistry(),
        batches=[[]],
        working_config_json="{}",
    )
    try:
        with TestClient(app) as client:
            r = client.post(
                "/api/config/patch-json",
                json={
                    "session_token": token,
                    "shallow_merge": {},
                    "pointer_sets": [{"path": "/missing/0/x", "value": 1}],
                },
            )
        assert r.status_code == 422
        data = r.json()
        assert data["ok"] is False
        assert data["errors"][0]["code"] == "pointer_set_failed"
    finally:
        sessions.pop(token, None)
