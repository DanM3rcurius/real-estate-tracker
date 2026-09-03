"""Liveness endpoint.

Answers with the two numbers that tell you whether the tracker is actually
tracking: how many properties it knows, and when it last ran.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hofradar.db.models import Property, SearchRun
from hofradar.web.deps import get_db

router = APIRouter(tags=["system"])


@router.get("/healthz")
def healthz(request: Request, session: Session = Depends(get_db)) -> dict[str, Any]:
    # This endpoint is public so a container healthcheck can reach it without a
    # session, which means an anonymous caller must not learn anything about the
    # search. Liveness only, unless they are signed in.
    from hofradar.web import auth

    if not auth.is_authenticated(request):
        return {"status": "ok"}

    try:
        properties = session.scalar(select(func.count(Property.id))) or 0
    except Exception:  # noqa: BLE001 - an unmigrated DB is still "up"
        properties = 0
    last_run = None
    try:
        run = session.scalars(select(SearchRun).order_by(SearchRun.started_at.desc())).first()
        if run is not None:
            last_run = {
                "id": run.id,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "status": run.status,
                "trigger": run.trigger,
            }
    except Exception:  # noqa: BLE001
        last_run = None
    return {"status": "ok", "properties": properties, "last_run": last_run}
