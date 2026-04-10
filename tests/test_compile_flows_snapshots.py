"""Golden snapshots for ``compile_flows`` → FlowIR (Plan 08 Track A).

Uses syrupy. Update intentionally: ``pytest --snapshot-update tests/test_compile_flows_snapshots.py``.
"""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import pytest

from flow_compiler import compile_flows
from models import DataLoaderConfig
from tests.compiler_snapshot_examples import COMPILER_SNAPSHOT_EXAMPLE_FILES
from tests.paths import EXAMPLES_DIR


def _flow_irs_snapshot_payload(flow_irs):
    """JSON-friendly structure: nested dataclasses → plain dict/list."""
    return [asdict(ir) for ir in flow_irs]


def _compile_example_json(name: str):
    path = EXAMPLES_DIR / name
    raw = path.read_text()
    config = DataLoaderConfig.model_validate_json(raw)
    assert config.funds_flows, f"{name} must include funds_flows"
    return compile_flows(list(config.funds_flows), config)


@pytest.mark.parametrize("example_file", COMPILER_SNAPSHOT_EXAMPLE_FILES)
def test_compile_flows_flow_ir_snapshot(example_file: str, snapshot):
    flow_irs = _compile_example_json(example_file)
    stem = Path(example_file).stem
    assert snapshot(name=stem) == _flow_irs_snapshot_payload(flow_irs)
