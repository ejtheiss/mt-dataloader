"""Plan 09 contract test via Schemathesis over HTTP transport."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

schemathesis = pytest.importorskip("schemathesis")


def _pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_openapi(base_url: str, timeout_s: float = 15.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base_url}/openapi.json", timeout=1.0)
            if r.status_code == 200:
                return
        except Exception:
            pass
        time.sleep(0.2)
    raise RuntimeError("Timed out waiting for uvicorn test server")


def test_schemathesis_runs_json_contract_http(tmp_path: Path) -> None:
    port = _pick_free_port()
    base_url = f"http://127.0.0.1:{port}"
    data_dir = tmp_path / "data"
    runs_dir = tmp_path / "runs"
    data_dir.mkdir(parents=True, exist_ok=True)
    runs_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["DATALOADER_DATA_DIR"] = str(data_dir)
    env["DATALOADER_RUNS_DIR"] = str(runs_dir)
    env["DATALOADER_NGROK_AUTO_START"] = "false"

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "dataloader.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    proc = subprocess.Popen(
        cmd,
        env=env,
        cwd=str(Path(__file__).resolve().parent.parent),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for_openapi(base_url)
        schema = schemathesis.openapi.from_uri(
            f"{base_url}/openapi.json",
            force_schema_version="30",
        ).include(
            path="/api/runs.json",
            method="GET",
        )
        op = next(schema.get_all_operations()).ok()
        cases = [
            op.make_case(query={"limit": 1, "offset": 0}),
            op.make_case(query={"limit": 20, "offset": 0}),
            op.make_case(query={"limit": 50, "sort": "status", "dir": "desc"}),
        ]
        for case in cases:
            case.call_and_validate(base_url=base_url)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
