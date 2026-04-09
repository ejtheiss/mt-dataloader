"""FastAPI dependencies — shared ``Depends`` wiring for templates and settings."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends, Form, Header, Query, Request
from jinja2_fragments.fastapi import Jinja2Blocks
from sqlalchemy.ext.asyncio import AsyncSession

from dataloader.session import SessionState, sessions
from dataloader.tunnel import TunnelManager
from models import AppSettings, CurrentAppUser, coerce_app_user_role


def get_settings(request: Request) -> AppSettings:
    return request.app.state.settings


def get_templates(request: Request) -> Jinja2Blocks:
    return request.app.state.templates


def get_tunnel(request: Request) -> TunnelManager:
    return request.app.state.tunnel


SettingsDep = Annotated[AppSettings, Depends(get_settings)]
TemplatesDep = Annotated[Jinja2Blocks, Depends(get_templates)]
TunnelDep = Annotated[TunnelManager, Depends(get_tunnel)]


def session_from_query_optional(session_token: str = Query("")) -> SessionState | None:
    if not session_token:
        return None
    return sessions.get(session_token)


def session_from_query_required(session_token: str = Query(...)) -> SessionState | None:
    return sessions.get(session_token)


def session_from_form(session_token: str = Form(...)) -> SessionState | None:
    return sessions.get(session_token)


def session_from_header(
    x_session_token: str | None = Header(default=None),
) -> SessionState | None:
    if not x_session_token:
        return None
    return sessions.get(x_session_token)


OptionalSessionQueryDep = Annotated[SessionState | None, Depends(session_from_query_optional)]
RequiredSessionQueryDep = Annotated[SessionState | None, Depends(session_from_query_required)]
SessionFormDep = Annotated[SessionState | None, Depends(session_from_form)]
SessionHeaderDep = Annotated[SessionState | None, Depends(session_from_header)]


async def get_db_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """One ``AsyncSession`` per request — Plan 0 (``db_session`` naming)."""
    factory = getattr(request.app.state, "async_session_factory", None)
    if factory is None:
        raise RuntimeError("Database not initialized (async_session_factory missing)")
    async with factory() as session:
        yield session


AsyncSessionDep = Annotated[AsyncSession, Depends(get_db_session)]


def get_current_app_user(request: Request) -> CurrentAppUser:
    """Plan 0 stub: single operator from app state until real auth."""
    uid = int(getattr(request.app.state, "default_user_id", 1))
    role = coerce_app_user_role(getattr(request.app.state, "default_user_role", None))
    return CurrentAppUser(id=uid, role=role)


CurrentAppUserDep = Annotated[CurrentAppUser, Depends(get_current_app_user)]
