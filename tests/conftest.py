"""Pytest wiring: ensure repo root is importable; shared path fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture
def repo_root() -> Path:
    return _REPO_ROOT


@pytest.fixture
def examples_dir() -> Path:
    return _REPO_ROOT / "examples"
