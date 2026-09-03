"""Pipeline run history, and the "Jetzt suchen" button.

The pipeline lives in another package that may not exist yet, so the button
checks importability *before* scheduling anything and says so in German rather
than throwing a 500 at a user who just wanted to search.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from hofradar.db.models import SearchRun
from hofradar.web import lazy
from hofradar.web.deps import get_db, profile_from_query, render

router = APIRouter(tags=["runs"])


def _runs(session: Session, limit: int = 50) -> list[SearchRun]:
    stmt = select(SearchRun).order_by(SearchRun.started_at.desc()).limit(limit)
    return list(session.scalars(stmt))


def _context(request: Request, session: Session, **extra: Any) -> dict[str, Any]:
    profile = profile_from_query(request.query_params, session=session)
    context: dict[str, Any] = {
        "profile": profile,
        "runs": _runs(session),
        "pipeline_available": lazy.is_available("hofradar.pipeline"),
        "degraded": [],
        "started": False,
    }
    context.update(extra)
    return context


@router.get("/runs")
def runs_page(request: Request, session: Session = Depends(get_db)):
    return render(request, "pages/runs.html", _context(request, session))


async def _execute(profile: Any) -> None:
    """Background worker. Never raises into the server loop."""
    try:
        run_pipeline = lazy.load("hofradar.pipeline:run_pipeline")
        result = run_pipeline(profile, trigger="web")
        if asyncio.iscoroutine(result):
            await result
    except Exception:  # noqa: BLE001 - surfaced through SearchRun / the runs page
        return


@router.post("/api/run")
def api_run(
    request: Request,
    background: BackgroundTasks,
    session: Session = Depends(get_db),
):
    profile = profile_from_query(request.query_params, session=session)
    try:
        lazy.load("hofradar.pipeline:run_pipeline")
    except lazy.ModuleUnavailable as exc:
        return render(
            request,
            "partials/run_status.html",
            {"degraded": [lazy.Degraded(exc.user_message)], "started": False},
            status_code=200,
        )
    background.add_task(_execute, profile)
    return render(
        request,
        "partials/run_status.html",
        {"degraded": [], "started": True, "profile": profile},
    )
