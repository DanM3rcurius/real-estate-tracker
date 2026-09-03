"""The web UI - the product surface.

Kept deliberately empty of imports: ``hofradar.web.filters`` and
``hofradar.web.history`` are pulled in by ``hofradar.report``, and importing
this package must therefore never drag in FastAPI or the route modules.
Use ``from hofradar.web.app import create_app`` to build the application.
"""

from __future__ import annotations

__all__ = ["create_app"]


def __getattr__(name: str):  # pragma: no cover - lazy re-export
    if name == "create_app":
        from hofradar.web.app import create_app

        return create_app
    raise AttributeError(name)
