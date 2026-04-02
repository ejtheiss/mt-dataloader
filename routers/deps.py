"""FastAPI dependencies — shared ``Depends`` wiring for templates and settings."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Form, Header, Query, Request
from fastapi.templating import Jinja2Templates

from models import AppSettings
from session import SessionState, sessions
from tunnel import TunnelManager


def get_settings(request: Request) -> AppSettings:
    return request.app.state.settings


def get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates


def get_tunnel(request: Request) -> TunnelManager:
    return request.app.state.tunnel


SettingsDep = Annotated[AppSettings, Depends(get_settings)]
TemplatesDep = Annotated[Jinja2Templates, Depends(get_templates)]
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
