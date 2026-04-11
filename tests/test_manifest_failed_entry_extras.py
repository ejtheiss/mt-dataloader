"""Optional fields on failure entries (backward compatible)."""

from __future__ import annotations

from pydantic import TypeAdapter

from models.run_execution_entries import FailedEntry


def test_failed_entry_optional_fields_default_none() -> None:
    e = FailedEntry(typed_ref="ledger.foo", error="boom", failed_at="2026-01-01T00:00:00+00:00")
    assert e.error_type is None
    assert e.http_status is None
    assert e.error_cause is None


def test_failed_entry_load_without_extras_from_root_dict() -> None:
    root = {
        "run_id": "run_test",
        "config_hash": "sha256:abc",
        "resources_failed": [
            {
                "typed_ref": "x",
                "error": "old format",
                "failed_at": "2026-01-01T00:00:00+00:00",
            }
        ],
    }
    rows = TypeAdapter(list[FailedEntry]).validate_python(root["resources_failed"])
    assert len(rows) == 1
    assert rows[0].error_cause is None


def test_failed_entry_with_extras() -> None:
    f = FailedEntry(
        typed_ref="ref.a",
        error="[ref.a] HTTP 422: bad",
        failed_at="2026-01-01T00:00:00+00:00",
        error_type="BadRequestError",
        http_status=422,
        error_cause="root: validation",
    )
    assert f.error_type == "BadRequestError"
    assert f.http_status == 422
    assert f.error_cause == "root: validation"
