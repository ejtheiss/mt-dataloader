"""Optional fields on manifest failure entries (backward compatible)."""

from __future__ import annotations

from models.manifest import FailedEntry, RunManifest


def test_failed_entry_optional_fields_default_none() -> None:
    e = FailedEntry(typed_ref="ledger.foo", error="boom", failed_at="2026-01-01T00:00:00+00:00")
    assert e.error_type is None
    assert e.http_status is None
    assert e.error_cause is None


def test_run_manifest_load_without_failure_extras() -> None:
    m = RunManifest.model_validate(
        {
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
    )
    assert len(m.resources_failed) == 1
    assert m.resources_failed[0].error_cause is None


def test_record_failure_passes_extras() -> None:
    m = RunManifest(run_id="r", config_hash="h")
    m.record_failure(
        "ref.a",
        "[ref.a] HTTP 422: bad",
        error_type="BadRequestError",
        http_status=422,
        error_cause="root: validation",
    )
    f = m.resources_failed[0]
    assert f.error_type == "BadRequestError"
    assert f.http_status == 422
    assert f.error_cause == "root: validation"
