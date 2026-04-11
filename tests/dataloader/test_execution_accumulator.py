"""Engine-internal execution state (``ExecutionAccumulator``)."""

from __future__ import annotations

from dataloader.engine.execution_accumulator import ExecutionAccumulator
from models.run_execution_entries import ManifestEntry


def test_accumulator_record_and_finalize() -> None:
    acc = ExecutionAccumulator(run_id="r1", config_hash="sha256:x")
    acc.record(
        ManifestEntry(
            batch=0,
            resource_type="ledger",
            typed_ref="ledgers.main",
            created_id="la_1",
            created_at="2026-01-01T00:00:00+00:00",
            deletable=False,
        )
    )
    acc.finalize("completed")
    assert acc.status == "completed"
    assert acc.completed_at is not None
    assert len(acc.resources_created) == 1
