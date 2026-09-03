"""The weekly digest.

Public surface (see ``docs/MODULE_API.md``)::

    build_report(session, profile, *, run_id=None, since=None) -> ReportData
    render_markdown(data) -> str
    render_html(data) -> str
    source_yield(session, *, since) -> list[SourceYield]

``ReportData`` (and its parts) are exported because they appear in those
signatures. ``save_report`` / ``latest_report`` / ``list_reports`` are exported
in addition to the contract because the blueprint requires the digest to be
persisted to ``ReportRecord`` and the web layer must reach that through the
package's public surface rather than by importing a submodule. ``SourceYield``
and ``source_yield`` answer a narrower question - was a given source worth
building - and are exported alongside the digest for the same reason.
"""

from __future__ import annotations

from hofradar.report.data import (
    ACTION_CALL,
    ACTION_CHECK,
    ACTION_WATCH,
    ReportCounts,
    ReportData,
    ReportEntry,
    build_report,
)
from hofradar.report.render import render_html, render_markdown
from hofradar.report.store import latest_report, list_reports, save_report
from hofradar.report.yield_stats import SourceYield, source_yield

__all__ = [
    "ACTION_CALL",
    "ACTION_CHECK",
    "ACTION_WATCH",
    "ReportCounts",
    "ReportData",
    "ReportEntry",
    "SourceYield",
    "build_report",
    "latest_report",
    "list_reports",
    "render_html",
    "render_markdown",
    "save_report",
    "source_yield",
]
