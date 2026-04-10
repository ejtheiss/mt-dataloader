"""Golden snapshots for ``compile_flows`` → ``emit_dataloader_config`` (Plan 08 / R1).

Pairs with ``test_compile_flows_snapshots.py``: IR goldens catch step-shape drift;
these catch emit/resource-shape drift. Update intentionally:

    pytest --snapshot-update tests/test_emit_dataloader_config_snapshots.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from flow_compiler import compile_flows, emit_dataloader_config
from models import DataLoaderConfig
from tests.compiler_snapshot_examples import COMPILER_SNAPSHOT_EXAMPLE_FILES
from tests.paths import EXAMPLES_DIR


def _canonicalize_json_obj(obj):
    """Sort dict keys recursively; preserve list order (semantic)."""
    if isinstance(obj, dict):
        return {k: _canonicalize_json_obj(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_canonicalize_json_obj(v) for v in obj]
    return obj


def _emit_snapshot_payload(config: DataLoaderConfig) -> object:
    """Round-trip through JSON so payload matches wire shape; keys canonicalized."""
    flow_irs = compile_flows(list(config.funds_flows), config)
    emitted = emit_dataloader_config(flow_irs, base_config=config)
    raw = emitted.model_dump_json(exclude_none=True)
    return _canonicalize_json_obj(json.loads(raw))


@pytest.mark.parametrize("example_file", COMPILER_SNAPSHOT_EXAMPLE_FILES)
def test_emit_dataloader_config_snapshot(example_file: str, snapshot):
    path = EXAMPLES_DIR / example_file
    config = DataLoaderConfig.model_validate_json(path.read_text())
    assert config.funds_flows, f"{example_file} must include funds_flows"
    stem = Path(example_file).stem
    assert snapshot(name=stem) == _emit_snapshot_payload(config)
