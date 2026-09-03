"""The weekly digest.

Public surface (see ``docs/MODULE_API.md``)::

    build_report(session, profile, *, run_id=None, since=None) -> ReportData
    render_markdown(data) -> str
    render_html(data) -> str
    source_yield(session, *, since, radius_air_km=None) -> list[SourceYield]
    coverage_by_municipality(session, *, since, expected) -> list[MunicipalityCoverage]

``ReportData`` (and its parts) are exported because they appear in those
signatures. ``save_report`` / ``latest_report`` / ``list_reports`` are exported
in addition to the contract because the blueprint requires the digest to be
persisted to ``ReportRecord`` and the web layer must reach that through the
package's public surface rather than by importing a submodule. ``SourceYield``
and ``source_yield`` answer a narrower question - was a given source worth
building - and are exported alongside the digest for the same reason.
``MunicipalityCoverage`` and ``coverage_by_municipality`` answer a related but
distinct question - which expected municipality produced nothing at all - and
are exported for the same reason: ``ReportData.municipality_coverage`` appears
in ``build_report``'s return value, so the type it is made of must be public.
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
from hofradar.report.yield_stats import (
    MunicipalityCoverage,
    SourceYield,
    coverage_by_municipality,
    source_yield,
)

__all__ = [
    "ACTION_CALL",
    "ACTION_CHECK",
    "ACTION_WATCH",
    "MunicipalityCoverage",
    "ReportCounts",
    "ReportData",
    "ReportEntry",
    "SourceYield",
    "build_report",
    "coverage_by_municipality",
    "latest_report",
    "list_reports",
    "render_html",
    "render_markdown",
    "save_report",
    "source_yield",
]
