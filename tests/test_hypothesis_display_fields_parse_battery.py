"""Hypothesis battery: why `funds_flows[].display_*` might still show extra_forbidden.

Each test names the hypothesis it checks. Run: pytest tests/test_hypothesis_display_fields_parse_battery.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from dataloader.main import app
from models import DataLoaderConfig
from models.flow_dsl import FundsFlowConfig

ROOT = Path(__file__).resolve().parent.parent
LENDING = ROOT / "examples" / "lending_platform.json"


def _lending_bytes() -> bytes:
    assert LENDING.is_file(), f"missing {LENDING}"
    return LENDING.read_bytes()


def test_h_same_process_model_fields_include_display() -> None:
    """H-A (partial): current interpreter's FundsFlowConfig includes Plan 10c fields."""
    assert "display_title" in FundsFlowConfig.model_fields
    assert "display_summary" in FundsFlowConfig.model_fields


def test_h_same_process_import_identity() -> None:
    """H-A: single FundsFlowConfig class object via models package vs flow_dsl."""
    from models import FundsFlowConfig as F_pkg

    assert F_pkg is FundsFlowConfig


def test_h_same_process_dataloader_parse_lending() -> None:
    """H-D: lending example keys sit on flow objects; parse succeeds in-process."""
    raw = json.loads(_lending_bytes())
    for i, flow in enumerate(raw["funds_flows"]):
        assert isinstance(flow, dict)
        for k in ("display_title", "display_summary"):
            if k in flow:
                assert k == k.encode("ascii").decode("ascii"), f"non-ascii key in flow[{i}]: {k!r}"
    DataLoaderConfig.model_validate(raw)


def test_h_subprocess_clean_env_parse_lending() -> None:
    """H-A: fresh Python process + PYTHONPATH=repo root (catches shadowed `models`, stale cwd)."""
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    code = (
        "import json; from pathlib import Path; "
        "from models import DataLoaderConfig; "
        "from models.flow_dsl import FundsFlowConfig; "
        "p = Path('examples/lending_platform.json'); "
        "DataLoaderConfig.model_validate(json.loads(p.read_text())); "
        "assert 'display_title' in FundsFlowConfig.model_fields; "
        "print(FundsFlowConfig.__module__)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, f"stderr={proc.stderr!r} stdout={proc.stdout!r}"


def test_h_subprocess_reports_flow_dsl_module_path() -> None:
    """H-A: prove which file backs FundsFlowConfig in a subprocess."""
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    code = (
        "import models.flow_dsl as m; "
        "from models.flow_dsl import FundsFlowConfig; "
        "import inspect; "
        "print(inspect.getfile(FundsFlowConfig)); "
        "print(m.__file__)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 2
    assert str(ROOT.resolve()) in lines[0] or lines[0].startswith("/")
    assert lines[0].endswith("flow_dsl.py") or "flow_dsl" in lines[0]


def test_h_http_validate_json_lending_via_testclient() -> None:
    """H-B: FastAPI app stack accepts lending body (same as curl to local server)."""
    with TestClient(app) as client:
        r = client.post("/api/validate-json", content=_lending_bytes())
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("schema_version") == 1
    assert data.get("ok") is True, data
    assert data.get("phase") == "complete"
    assert data.get("errors") == []


def test_h_http_schema_includes_funds_flow_display_properties() -> None:
    """H-F: live OpenAPI/JSON schema from app includes display_* on FundsFlowConfig."""
    with TestClient(app) as client:
        r = client.get("/api/schema")
    assert r.status_code == 200
    schema = r.json()
    defs = schema.get("$defs") or {}
    ffc = defs.get("FundsFlowConfig") or {}
    props = ffc.get("properties") or {}
    assert "display_title" in props, f"FundsFlowConfig.properties keys sample: {list(props)[:20]}"
    assert "display_summary" in props


def test_h_app_module_loaded_models_still_expose_display_fields() -> None:
    """H-G: same interpreter after `dataloader.main` import (uvicorn-like), fields remain."""
    import dataloader.main  # noqa: F401 — ensure app import graph exercised
    from models.flow_dsl import FundsFlowConfig as F2

    assert "display_title" in F2.model_fields


def test_h_no_static_openapi_json_fork_in_repo() -> None:
    """H-F (repo scan): no checked-in OpenAPI snapshot that could stale-validate."""
    bad = list(ROOT.glob("**/openapi.json")) + list(ROOT.glob("**/openapi*.yaml"))
    # allow none or only under plan/ (untracked) — we only care tracked roots
    tracked_candidates = [p for p in bad if "plan" not in p.parts and ".hypothesis" not in p.parts]
    assert not tracked_candidates, f"unexpected static API artifacts: {tracked_candidates}"


@pytest.mark.skip(reason="Requires cluster access")
def test_h_kubernetes_split_revisions() -> None:
    """H-A replica skew: not testable in this workspace without k8s."""


@pytest.mark.skip(reason="Requires browser / client harness")
def test_h_client_side_ajv_not_in_repo() -> None:
    """H-B client-only: not testable headlessly here."""


def test_h_live_port_8000_must_agree_when_env_and_listener() -> None:
    """H-A stale process: if something listens on :8000 and RUN_HYPOTHESIS_LIVE_8000=1, it must parse lending.

    Manual run (from repo root) reproduced: TestClient passes while ``curl`` to a long-lived
    ``uvicorn`` returned ``extra_forbidden`` until the server process was restarted — the worker
    had imported an older ``FundsFlowConfig`` before ``display_*`` existed on disk.
    """
    if os.environ.get("RUN_HYPOTHESIS_LIVE_8000") != "1":
        pytest.skip("set RUN_HYPOTHESIS_LIVE_8000=1 to assert against a real listener on 8000")
    import socket
    import urllib.error
    import urllib.request

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.settimeout(0.5)
        if s.connect_ex(("127.0.0.1", 8000)) != 0:
            pytest.skip("nothing listening on 127.0.0.1:8000")
    finally:
        s.close()

    req = urllib.request.Request(
        "http://127.0.0.1:8000/api/validate-json",
        data=_lending_bytes(),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = json.loads(e.read().decode())

    assert body.get("ok") is True, (
        "listener on :8000 returned a failing validate-json envelope. "
        "Typical cause: uvicorn started before Plan 10c model fields landed — restart the server. "
        f"phase={body.get('phase')!r} errors={body.get('errors')!r}"
    )
    assert body.get("phase") == "complete"
