"""Liveness endpoint.

Answers with the two numbers that tell you whether the tracker is actually
tracking: how many properties it knows, and when it last ran.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hofradar.db.models import Property, SearchRun
from hofradar.web.deps import get_db

router = APIRouter(tags=["system"])


@router.get("/healthz")
def healthz(session: Session = Depends(get_db)) -> dict[str, Any]:
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
