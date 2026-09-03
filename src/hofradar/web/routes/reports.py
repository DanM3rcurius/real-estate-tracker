"""The weekly digest, on screen.

Generation is delegated to ``hofradar.report``; this module only decides which
report to show and offers the markdown for copying. A stored report is never
re-rendered from today's slider positions - it is a record of what the system
said that week.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from hofradar.web import lazy
from hofradar.web.deps import get_db, profile_from_query, render

router = APIRouter(tags=["report"])


def _list_reports(session: Session):
    records, _ = lazy.call_or("hofradar.report:list_reports", [], session)
    return records or []


@router.get("/report")
def report_index(request: Request, session: Session = Depends(get_db)):
    profile = profile_from_query(request.query_params, session=session)
    records = _list_reports(session)
    degraded: list[lazy.Degraded] = []

    record = records[0] if records else None
    markdown = record.markdown if record is not None else None
    html = record.html if record is not None else None

    if record is None:
        # Nothing stored yet: render a live preview so the page is never empty.
        data, note = lazy.call_or("hofradar.report:build_report", None, session, profile)
        if note is not None:
            degraded.append(note)
        else:
            markdown, md_note = lazy.call_or("hofradar.report:render_markdown", None, data)
            html, html_note = lazy.call_or("hofradar.report:render_html", None, data)
            for candidate in (md_note, html_note):
                if candidate is not None:
                    degraded.append(candidate)

    return render(
        request,
        "pages/report.html",
        {
            "profile": profile,
            "record": record,
            "records": records,
            "markdown": markdown,
            "report_html": html,
            "degraded": degraded,
            "is_preview": record is None,
        },
    )


@router.get("/report/{report_id}")
def report_detail(report_id: int, request: Request, session: Session = Depends(get_db)):
    from hofradar.db.models import ReportRecord

    profile = profile_from_query(request.query_params, session=session)
    record = session.get(ReportRecord, report_id)
    if record is None:
        return render(
            request,
            "pages/error.html",
            {"code": 404, "message": f"Kein Report mit der ID {report_id}."},
            status_code=404,
        )
    return render(
        request,
        "pages/report.html",
        {
            "profile": profile,
            "record": record,
            "records": _list_reports(session),
            "markdown": record.markdown,
            "report_html": record.html,
            "degraded": [],
            "is_preview": False,
        },
    )


@router.get("/report/{report_id}/markdown", response_class=PlainTextResponse)
def report_markdown(report_id: int, session: Session = Depends(get_db)) -> PlainTextResponse:
    from hofradar.db.models import ReportRecord

    record = session.get(ReportRecord, report_id)
    if record is None:
        return PlainTextResponse("Nicht gefunden.", status_code=404)
    return PlainTextResponse(record.markdown or "", media_type="text/markdown; charset=utf-8")


@router.post("/report")
def report_generate(request: Request, session: Session = Depends(get_db)):
    """Build and persist this week's digest under the current profile."""
    profile = profile_from_query(request.query_params, session=session)
    degraded: list[lazy.Degraded] = []

    data, note = lazy.call_or("hofradar.report:build_report", None, session, profile)
    record = None
    markdown = html = None
    if note is not None:
        degraded.append(note)
    else:
        record, save_note = lazy.call_or("hofradar.report:save_report", None, session, data)
        if save_note is not None:
            degraded.append(save_note)
        else:
            markdown, html = record.markdown, record.html

    return render(
        request,
        "pages/report.html",
        {
            "profile": profile,
            "record": record,
            "records": _list_reports(session),
            "markdown": markdown,
            "report_html": html,
            "degraded": degraded,
            "is_preview": record is None,
            "just_generated": record is not None,
        },
    )
