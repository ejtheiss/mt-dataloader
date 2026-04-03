"""Stub app user until real auth — maps to ``users`` row (id + role)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

AppUserRole = Literal["admin", "user"]


def coerce_app_user_role(raw: str | None) -> AppUserRole:
    """Normalize DB string to ``admin`` or ``user`` (unknown → ``user``)."""
    if raw == "admin":
        return "admin"
    return "user"


class CurrentAppUser(BaseModel):
    """Injected per request from app state (default operator until login exists)."""

    model_config = {"frozen": True}

    id: int = Field(ge=1)
    role: AppUserRole

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"
