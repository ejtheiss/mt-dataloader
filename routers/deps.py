"""FastAPI dependencies — shared ``Depends`` wiring for templates and settings."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates

from models import AppSettings
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
