"""Render the app to a directory of static files.

The point of this module is that it does **not** know how to draw anything. It
boots the real FastAPI application, asks it for each page over an in-process
transport, and writes the bytes out. There is therefore no second rendering
path that can drift from the first - the same discipline ``routes/radar.py``
already applies when it serves the HTMX partial and the full page from one
``build_results``.

What a static host cannot do, and what this does about it:

* **No server.** Anything that posts is removed at the template level via the
  ``static_export`` flag, not stripped out of the HTML afterwards. Guarding the
  templates means the snapshot is correct by construction and says so in German,
  rather than being correct because a regex found every button.
* **No root.** A GitHub Pages project site lives under ``/<repo>/``, so every
  root-absolute URL in the markup is wrong by exactly that prefix. They are
  rewritten here. ``<base href>`` cannot do this job: it only affects relative
  URLs. The one link JavaScript builds at runtime reads ``window.HOFRADAR_BASE``
  instead, which this module injects.
* **No password.** The gate cannot exist on a static host, so everything
  exported is world-readable. That is why the workflow points this at synthetic
  data, and why the banner is not optional.

The sliders stay live. ``app.js`` recomputes the derived driving and purchase
bands client-side, so dragging still demonstrates the thing the product is; only
the result list underneath is frozen.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select

log = logging.getLogger(__name__)

PACKAGE_DIR = Path(__file__).parent
STATIC_DIR = PACKAGE_DIR / "static"

#: Pages that do not depend on a property id. ``None`` means "write to
#: <name>/index.html"; a string is an explicit output path.
STATIC_ROUTES: tuple[tuple[str, str], ...] = (
    ("/", "index.html"),
    ("/map", "map/index.html"),
    ("/report", "report/index.html"),
    ("/runs", "runs/index.html"),
    ("/api/properties.json", "api/properties.json"),
    ("/api/export.csv", "api/export.csv"),
)

#: href="/x", src="/x", action="/x" - but never href="//cdn" (protocol-relative)
#: and never an absolute http(s) URL, which do not start with a single slash.
_ROOT_URL_RE = re.compile(r'\b(href|src|action)="/(?!/)')

_HEAD_CLOSE = "</head>"


@dataclass(slots=True)
class ExportResult:
    """What an export produced, so a caller can assert on it."""

    destination: Path
    pages: list[str] = field(default_factory=list)
    properties: int = 0
    assets: int = 0

    @property
    def page_count(self) -> int:
        return len(self.pages)


class ExportError(RuntimeError):
    """A page the snapshot needs did not render."""


def _normalise_base(base_path: str) -> str:
    """``"real-estate-tracker"`` and ``"/real-estate-tracker/"`` mean the same."""
    stripped = base_path.strip().strip("/")
    return f"/{stripped}" if stripped else ""


def rewrite_urls(html: str, base_path: str) -> str:
    """Prefix every root-absolute URL in the markup with the base path."""
    if not base_path:
        return html
    return _ROOT_URL_RE.sub(rf'\1="{base_path}/', html)


def inject_base_global(html: str, base_path: str) -> str:
    """Tell ``app.js`` where it lives, for the links it builds at runtime."""
    if _HEAD_CLOSE not in html:
        return html
    snippet = f"<script>window.HOFRADAR_BASE={json.dumps(base_path)};</script>\n"
    return html.replace(_HEAD_CLOSE, snippet + _HEAD_CLOSE, 1)


def _copy_static(destination: Path) -> int:
    """Copy the vendored assets. Nothing is fetched from a CDN (DECISIONS 7)."""
    target = destination / "static"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(STATIC_DIR, target)
    return sum(1 for path in target.rglob("*") if path.is_file())


def _write(destination: Path, relative: str, body: bytes) -> None:
    path = destination / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)


def _property_ids(session_factory: Any) -> list[str]:
    from hofradar.db.models import Property

    with session_factory() as session:
        return list(
            session.scalars(select(Property.public_id).order_by(Property.id)).all()
        )


def export_site(
    destination: Path | str,
    *,
    base_path: str = "",
    session_factory: Any | None = None,
    built_at: datetime | None = None,
) -> ExportResult:
    """Write a browsable static copy of the app to ``destination``.

    Every page is fetched from the real app and must answer 200; a route that
    fails raises rather than publishing a broken snapshot.
    """
    from fastapi.testclient import TestClient

    from hofradar.web.app import create_app

    destination = Path(destination)
    base = _normalise_base(base_path)
    stamp = (built_at or datetime.now(UTC)).strftime("%d.%m.%Y")

    if session_factory is None:
        from sqlalchemy.orm import sessionmaker

        from hofradar.db.session import get_engine, init_db

        engine = get_engine()
        init_db(engine)
        session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)

    app = create_app(
        session_factory=session_factory,
        static_export=True,
        snapshot_built_at=stamp,
    )
    result = ExportResult(destination=destination)

    destination.mkdir(parents=True, exist_ok=True)
    public_ids = _property_ids(session_factory)
    result.properties = len(public_ids)

    routes: list[tuple[str, str]] = [
        *STATIC_ROUTES,
        *((f"/property/{pid}", f"property/{pid}/index.html") for pid in public_ids),
    ]

    with TestClient(app) as client:
        for url, relative in routes:
            response = client.get(url)
            if response.status_code != 200:
                raise ExportError(
                    f"{url} returned {response.status_code}; refusing to publish "
                    "a snapshot with a broken page"
                )
            body = response.content
            if relative.endswith(".html"):
                html = inject_base_global(rewrite_urls(response.text, base), base)
                body = html.encode("utf-8")
            _write(destination, relative, body)
            result.pages.append(relative)

    result.assets = _copy_static(destination)

    # Pages would otherwise run the output through Jekyll, which ignores
    # directories whose names begin with an underscore.
    _write(destination, ".nojekyll", b"")

    log.info(
        "export: %d pages, %d properties, %d assets -> %s",
        result.page_count,
        result.properties,
        result.assets,
        destination,
    )
    return result
