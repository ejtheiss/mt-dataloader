"""FastAPI application for the Modern Treasury Dataloader.

Configures the app, mounts static files, sets up templates, and
includes all APIRouter modules. Route handlers live in ``dataloader/routers/``.

Paths (templates, static, logs) resolve from the **repository root**, not the
package directory — same behavior as the former root ``main.py``.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from loguru import logger
from sqlalchemy import text

from _version import __version__
from dataloader.routers.cleanup import router as cleanup_router
from dataloader.routers.connection import router as connection_router
from dataloader.routers.execute import router as execute_router
from dataloader.routers.flows import router as flows_router
from dataloader.routers.runs import router as runs_router
from dataloader.routers.setup import router as setup_router
from dataloader.routers.tunnel import router as tunnel_router
from dataloader.webhooks import rebuild_correlation_index
from dataloader.webhooks import router as webhook_router
from db.database import (
    build_sqlite_file_urls,
    create_async_engine_and_sessionmaker,
    run_alembic_upgrade,
)
from helpers import set_templates
from models import AppSettings
from mt_doc_links import MT_DOCS
from tunnel import NgrokStartError, TunnelManager

# Repository root (parent of ``dataloader/`` package).
_REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Template engine
# ---------------------------------------------------------------------------

templates = Jinja2Templates(directory=str(_REPO_ROOT / "templates"))

templates.env.globals["mt_docs"] = MT_DOCS

_css_path = _REPO_ROOT / "static" / "style.css"


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

    data_path = Path(settings.data_dir).expanduser().resolve()
    data_path.mkdir(parents=True, exist_ok=True)
    sqlite_file = data_path / "dataloader.sqlite"
    sync_url, async_url = build_sqlite_file_urls(sqlite_file)
    try:
        run_alembic_upgrade(_REPO_ROOT, sync_url)
    except Exception as exc:
        logger.exception("Alembic upgrade failed: {}", exc)
        raise

    engine = None
    tunnel_mgr = None
    try:
        engine, session_factory = create_async_engine_and_sessionmaker(async_url)
        app.state.async_engine = engine
        app.state.async_session_factory = session_factory
        async with session_factory() as _s:
            result = await _s.execute(text("SELECT id FROM users ORDER BY id ASC LIMIT 1"))
            row = result.first()
        app.state.default_user_id = int(row[0]) if row else 1
        logger.info("SQLite at {} (default user id={})", sqlite_file, app.state.default_user_id)

        # Single-writer assumption: index + session store match one process (see dataloader/session/).
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
    finally:
        if tunnel_mgr is not None:
            tunnel_mgr.stop()
        if engine is not None:
            await engine.dispose()
        logger.info("Dataloader shutting down")


# ---------------------------------------------------------------------------
# App creation
# ---------------------------------------------------------------------------

app = FastAPI(title="MT Dataloader", version=__version__, lifespan=lifespan)


@app.get("/api/version", include_in_schema=False)
async def get_version():
    return {"version": __version__}


static_dir = _REPO_ROOT / "static"
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
