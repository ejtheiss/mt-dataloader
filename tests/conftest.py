"""Pytest wiring: ensure repo root is importable; shared path fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture(scope="session", autouse=True)
def _dataloader_data_dir(tmp_path_factory) -> Path:
    """Isolate SQLite + Alembic under a temp dir for the whole test session."""
    import os

    p = tmp_path_factory.mktemp("dataloader_data")
    os.environ["DATALOADER_DATA_DIR"] = str(p)
    return p


@pytest.fixture
def repo_root() -> Path:
    return _REPO_ROOT


@pytest.fixture
def examples_dir() -> Path:
    return _REPO_ROOT / "examples"
