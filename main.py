"""FastAPI application factory for the Modern Treasury Dataloader.

Configures the app, mounts static files, sets up templates, and
includes all APIRouter modules.  Route handlers live in ``routers/``.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger

from _version import __version__
from helpers import set_templates
from models import AppSettings
from mt_doc_links import MT_DOCS
from routers.cleanup import router as cleanup_router
from routers.connection import router as connection_router
from routers.execute import router as execute_router
from routers.flows import router as flows_router
from routers.runs import router as runs_router
from routers.setup import router as setup_router
from routers.tunnel import router as tunnel_router
from tunnel import NgrokStartError, TunnelManager
from webhooks import rebuild_correlation_index
from webhooks import router as webhook_router

# Paths relative to this file — not the process cwd (Docker/K8s may use another cwd).
_APP_ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Template engine
# ---------------------------------------------------------------------------

templates = Jinja2Templates(directory=str(_APP_ROOT / "templates"))

templates.env.globals["mt_docs"] = MT_DOCS

_css_path = _APP_ROOT / "static" / "style.css"


def _css_version() -> str:
    try:
        return str(int(_css_path.stat().st_mtime))
    except OSError:
        return "1"


templates.env.globals["css_version"] = _css_version()
templates.env.globals["app_version"] = __version__

set_templates(templates)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _configure_logging(settings: AppSettings) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=settings.log_level,
        format=(
            "<green>{time:HH:mm:ss}</green> | "
            "<level>{level:<8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> - "
            "<level>{message}</level>"
        ),
        colorize=True,
    )
    logger.add(
        "logs/dataloader_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        serialize=True,
        rotation="10 MB",
        retention="7 days",
    )


# ---------------------------------------------------------------------------
# App lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = AppSettings()
    app.state.settings = settings
    _configure_logging(settings)
    # Single-writer assumption: index + session store match one process (see session.py).
    rebuild_correlation_index(settings.runs_dir)

    tunnel_mgr = TunnelManager(runs_dir=settings.runs_dir)
    app.state.tunnel = tunnel_mgr

    authtoken = settings.ngrok_authtoken or tunnel_mgr.saved_authtoken
    if authtoken and settings.ngrok_auto_start:
        try:
            domain = settings.ngrok_domain or tunnel_mgr.saved_domain or None
            url = tunnel_mgr.start(authtoken, domain=domain)
            logger.info("Tunnel auto-started: {}", url)
        except NgrokStartError as exc:
            logger.warning(
                "Tunnel auto-start failed ({}): {} — start manually from /listen or free agent slots.",
                exc.code or "ngrok",
                exc,
            )
        except Exception as exc:
            logger.warning("Tunnel auto-start failed (start manually from /listen): {}", exc)

    logger.info("Dataloader v{} started", __version__)
    yield
    tunnel_mgr.stop()
    logger.info("Dataloader shutting down")


# ---------------------------------------------------------------------------
# App creation
# ---------------------------------------------------------------------------

app = FastAPI(title="MT Dataloader", version=__version__, lifespan=lifespan)


@app.get("/api/version", include_in_schema=False)
async def get_version():
    return {"version": __version__}


static_dir = _APP_ROOT / "static"
if static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
else:
    logger.warning(
        "Static directory missing at {} — UI will load without CSS "
        "(check image build and working directory).",
        static_dir,
    )

app.state.templates = templates

app.include_router(setup_router)
app.include_router(connection_router)
app.include_router(flows_router)
app.include_router(execute_router)
app.include_router(runs_router)
app.include_router(cleanup_router)
app.include_router(webhook_router)
app.include_router(tunnel_router)
