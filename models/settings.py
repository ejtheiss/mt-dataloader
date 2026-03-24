"""Application settings loaded from environment variables."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppSettings(BaseSettings):
    """Application configuration loaded from env vars / ``.env`` file.

    All variables are prefixed with ``DATALOADER_`` (e.g.
    ``DATALOADER_MT_API_KEY``).  The API key and org ID can also be supplied
    per-request from the UI form, overriding env defaults.
    """

    model_config = SettingsConfigDict(
        env_prefix="DATALOADER_",
        env_file=".env",
        env_file_encoding="utf-8",
    )

    mt_api_key: str = ""
    mt_org_id: str = ""
    runs_dir: str = "runs"
    log_level: str = "INFO"
    stamp_loader_metadata: bool = False
    max_concurrent_requests: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Max concurrent MT API calls within a batch",
    )
    webhook_secret: str = ""
    generation_chunk_size: int = Field(
        default=100,
        ge=10,
        le=500,
        description="Resources per DAG batch during generation runs",
    )
