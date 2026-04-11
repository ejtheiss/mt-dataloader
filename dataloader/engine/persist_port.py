"""Run execution persistence boundary (implementations live under ``db.repositories``)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from models.manifest import ManifestEntry


class RunStatePersistPort(Protocol):
    async def set_config_json(self, run_id: str, config_json: str) -> None:
        """Persist config snapshot once per run."""

    async def append_staged_item(
        self,
        run_id: str,
        typed_ref: str,
        resource_type: str,
        staged_at: str,
        payload_json: str,
    ) -> None:
        """Record a staged resource and resolved payload."""

    async def append_created(self, run_id: str, entry: ManifestEntry) -> None:
        """Persist a created resource row."""

    async def append_failure(
        self,
        run_id: str,
        typed_ref: str,
        error: str,
        *,
        failed_at: str,
        error_type: str | None,
        http_status: int | None,
        error_cause: str | None,
    ) -> None:
        """Record a failed ref."""

    async def finalize(
        self,
        run_id: str,
        status: str,
        completed_at: str | None,
        *,
        resources_created_count: int,
        resources_staged_count: int,
        resources_failed_count: int,
    ) -> None:
        """Terminal run row update (counts + status)."""
