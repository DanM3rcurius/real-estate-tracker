"""Persisting a digest.

A report is evidence of what the system said on a given Sunday, so it is stored
rendered - markdown *and* html - rather than regenerated on demand. Regenerating
it later against a moved slider would silently rewrite history.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from hofradar.db.models import ReportRecord
from hofradar.report.data import ReportData
from hofradar.report.render import render_html, render_markdown


def save_report(session: Session, data: ReportData, *, commit: bool = True) -> ReportRecord:
    """Write (or refresh) the :class:`ReportRecord` for this week and profile."""
    record = session.scalar(
        select(ReportRecord)
        .where(ReportRecord.week_label == data.week_label)
        .where(ReportRecord.profile_hash == data.profile_hash)
    )
    if record is None:
        record = ReportRecord(week_label=data.week_label, profile_hash=data.profile_hash)
        session.add(record)

    record.run_id = data.run_id
    record.generated_at = data.generated_at
    record.period_start = data.period_start
    record.summary = data.summary()
    record.markdown = render_markdown(data)
    record.html = render_html(data)

    session.flush()
    if commit:
        session.commit()
    return record


def latest_report(session: Session, *, profile_hash: str | None = None) -> ReportRecord | None:
    stmt = select(ReportRecord).order_by(ReportRecord.generated_at.desc())
    if profile_hash:
        stmt = stmt.where(ReportRecord.profile_hash == profile_hash)
    return session.scalars(stmt).first()


def list_reports(session: Session, *, limit: int = 30) -> list[ReportRecord]:
    stmt = select(ReportRecord).order_by(ReportRecord.generated_at.desc()).limit(limit)
    return list(session.scalars(stmt))
