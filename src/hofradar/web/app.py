"""FastAPI application factory.

Everything the app needs is injected here rather than looked up globally, so a
test can hand in an in-memory SQLite factory and the real ``data/`` directory is
never touched. Route modules are imported inside :func:`create_app` for the same
reason the rest of the web package imports lazily: a sibling package that is
still being written must not be able to stop the UI from booting.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from hofradar.web.filters import JINJA_FILTERS

PACKAGE_DIR = Path(__file__).parent
TEMPLATE_DIR = PACKAGE_DIR / "templates"
STATIC_DIR = PACKAGE_DIR / "static"

log = logging.getLogger(__name__)

APP_TITLE = "Hofradar"
APP_SUBTITLE = "Hofstellen im Blick behalten"

NAV_ITEMS = (
    ("/", "Radar", "🎯"),
    ("/map", "Karte", "🗺"),
    ("/report", "Report", "📰"),
    ("/runs", "Läufe", "⚙️"),
    ("/add", "Hinzufügen", "➕"),
    ("/settings", "Einstellungen", "🎚"),
)


def build_templates() -> Jinja2Templates:
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    templates.env.filters.update(JINJA_FILTERS)
    templates.env.globals.update(
        app_title=APP_TITLE,
        app_subtitle=APP_SUBTITLE,
        nav_items=NAV_ITEMS,
    )
    templates.env.trim_blocks = True
    templates.env.lstrip_blocks = True
    return templates


def _default_session_factory() -> sessionmaker[Session]:
    """Boot against the configured database, creating tables on first use."""
    from hofradar.db.session import get_engine, init_db

    engine = get_engine()
    init_db(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def create_app(
    *,
    session_factory: Any | None = None,
    engine: Engine | None = None,
    create_tables: bool = False,
    static_export: bool = False,
    snapshot_built_at: str = "",
) -> FastAPI:
    """Build the application.

    ``session_factory`` wins over ``engine``; passing neither uses the real
    database from ``HOFRADAR_DATABASE_URL`` / ``data/hofradar.sqlite3``.

    ``static_export`` is for :mod:`hofradar.web.export` only. It does not change
    what any route computes - the snapshot must show what the app shows - it
    only tells the templates to leave out the controls that would post to a
    server that will not be there, and to carry the snapshot banner.
    """
    if session_factory is None:
        if engine is not None:
            if create_tables:
                from hofradar.db.session import init_db

                init_db(engine)
            session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
        else:
            session_factory = _default_session_factory()

    app = FastAPI(title=APP_TITLE, docs_url="/api/docs", redoc_url=None)
    app.state.session_factory = session_factory
    app.state.templates = build_templates()
    app.state.static_export = static_export
    app.state.snapshot_built_at = snapshot_built_at
    if static_export:
        # Both write UIs. Neither has anything to show without a server.
        app.state.templates.env.globals["nav_items"] = tuple(
            item for item in NAV_ITEMS if item[0] not in ("/add", "/settings")
        )

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from hofradar.web.routes import (
        add,
        dossier,
        health,
        login,
        map_view,
        radar,
        reports,
        runs,
        settings,
    )

    for module in (radar, dossier, map_view, reports, runs, add, settings, health):
        app.include_router(module.router)

    # Authentication is opt-in: with no password configured the gate is not
    # installed at all, so a localhost run stays as frictionless as before.
    from hofradar.web import auth

    if auth.password_configured():
        app.include_router(login.router)
        app.add_middleware(auth.PasswordGateMiddleware)
        log.info("password gate enabled")
    else:
        log.warning(
            "no HOFRADAR_PASSWORD or HOFRADAR_PASSWORD_HASH set - the UI is open to "
            "anyone who can reach it. Fine on localhost, not on a public URL."
        )

    @app.exception_handler(404)
    async def _not_found(request: Request, exc: Exception) -> HTMLResponse:  # noqa: ARG001
        from hofradar.web.deps import render

        return render(
            request,
            "pages/error.html",
            {"code": 404, "message": "Diese Seite gibt es nicht."},
            status_code=404,
        )

    return app


def __getattr__(name: str) -> Any:
    """``uvicorn hofradar.web.app:app`` without building the app at import time."""
    if name == "app":
        application = create_app()
        globals()["app"] = application
        return application
    raise AttributeError(name)
