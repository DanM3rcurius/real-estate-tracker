"""FastAPI application factory.

Everything the app needs is injected here rather than looked up globally, so a
test can hand in an in-memory SQLite factory and the real ``data/`` directory is
never touched. Route modules are imported inside :func:`create_app` for the same
reason the rest of the web package imports lazily: a sibling package that is
still being written must not be able to stop the UI from booting.
"""

from __future__ import annotations

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
) -> FastAPI:
    """Build the application.

    ``session_factory`` wins over ``engine``; passing neither uses the real
    database from ``HOFRADAR_DATABASE_URL`` / ``data/hofradar.sqlite3``.
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

    STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    from hofradar.web.routes import (
        add,
        dossier,
        health,
        map_view,
        radar,
        reports,
        runs,
        settings,
    )

    for module in (radar, dossier, map_view, reports, runs, add, settings, health):
        app.include_router(module.router)

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
