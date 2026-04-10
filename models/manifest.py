"""Run manifest and manifest entry models — Pydantic for JSON round-trip."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from jsonutil import dumps_pretty, loads_path

if TYPE_CHECKING:
    from models.config import DataLoaderConfig


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ManifestEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    batch: int
    resource_type: str
    typed_ref: str
    created_id: str
    created_at: str
    deletable: bool
    child_refs: dict[str, str] = Field(default_factory=dict)
    cleanup_status: str | None = None


class FailedEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    typed_ref: str
    error: str
    failed_at: str
    error_type: str | None = None
    http_status: int | None = None
    error_cause: str | None = None


class StagedEntry(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    resource_type: str
    typed_ref: str
    staged_at: str


class RunManifest(BaseModel):
    """Mutable manifest accumulator. Written incrementally during execution."""

    model_config = ConfigDict(extra="ignore")

    run_id: str
    config_hash: str
    started_at: str = Field(default_factory=_now_iso)
    completed_at: str | None = None
    status: str = "running"
    resources_created: list[ManifestEntry] = Field(default_factory=list)
    resources_failed: list[FailedEntry] = Field(default_factory=list)
    resources_staged: list[StagedEntry] = Field(default_factory=list)
    generation_recipe: dict[str, Any] | None = None
    compile_id: str | None = None
    seed_version: str | None = None
    mt_org_id: str | None = None
    mt_org_label: str | None = None

    def record(self, entry: ManifestEntry) -> None:
        self.resources_created.append(entry)

    def record_failure(
        self,
        typed_ref: str,
        error: str,
        *,
        error_type: str | None = None,
        http_status: int | None = None,
        error_cause: str | None = None,
    ) -> None:
        self.resources_failed.append(
            FailedEntry(
                typed_ref=typed_ref,
                error=error,
                failed_at=_now_iso(),
                error_type=error_type,
                http_status=http_status,
                error_cause=error_cause,
            )
        )

    def record_staged(self, typed_ref: str, resource_type: str) -> None:
        self.resources_staged.append(
            StagedEntry(
                resource_type=resource_type,
                typed_ref=typed_ref,
                staged_at=_now_iso(),
            )
        )

    def finalize(self, status: str) -> None:
        self.status = status
        self.completed_at = _now_iso()

    def _to_dict(self) -> dict[str, Any]:
        """Backward-compatible alias for tests and legacy callers."""
        return self.model_dump(mode="json")

    def write(self, runs_dir: str) -> Path:
        """Write manifest to ``runs/<run_id>.json``. Creates dir if needed."""
        dirpath = Path(runs_dir)
        dirpath.mkdir(parents=True, exist_ok=True)
        file_path = dirpath / f"{self.run_id}.json"
        file_path.write_text(dumps_pretty(self._to_dict()), encoding="utf-8")
        return file_path

    def verify_hash(self, config: DataLoaderConfig) -> bool:
        """Check that a config matches the hash recorded at run start."""
        canonical = config.model_dump_json(exclude_none=True)
        h = f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"
        return self.config_hash == h

    @classmethod
    def load(cls, path: str | Path) -> RunManifest:
        """Load a manifest from a JSON file for resume or cleanup."""
        path = Path(path)
        data = loads_path(path)
        if not data.get("run_id"):
            data["run_id"] = path.stem.replace("manifest_", "")
        return cls.model_validate(data)
