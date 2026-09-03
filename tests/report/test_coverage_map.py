"""Which municipalities inside the radius produced nothing at all?

Zero observations from a town is ambiguous on its own - a quiet market and an
uncovered one look identical. Naming the expected municipalities up front is
what turns the silence into a finding.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hofradar.db.models import Observation
from hofradar.report.data import ReportCounts, ReportData
from hofradar.report.render import render_html, render_markdown
from hofradar.report.yield_stats import MunicipalityCoverage, coverage_by_municipality

EXPECTED = ["Feldkirchen-Westerham", "Bruckmühl", "Weyarn", "Holzkirchen"]
SINCE = datetime.now(UTC) - timedelta(days=28)


def _make_observation(session, *, property, source, scraped_at=None):
    """Append-only crawl record, built the way ingest would build one.

    ``Observation`` has no test factory of its own (only ``make_property`` and
    ``make_source`` exist in the shared conftest), so this mirrors the fields
    ``hofradar.lifecycle.ingest`` sets - see ``tests/report/test_yield_stats.py``,
    which does the same thing for the same reason.
    """
    kwargs = {}
    if scraped_at is not None:
        kwargs["scraped_at"] = scraped_at
    observation = Observation(
        property_id=property.id,
        source_id=source.id,
        url=f"https://{source.key}.example/{property.public_id}",
        title=property.canonical_title,
        town=property.town,
        postcode=property.postcode,
        **kwargs,
    )
    session.add(observation)
    session.flush()
    return observation


def test_a_municipality_with_no_observations_reports_zero(session, make_source, make_property) -> None:
    source = make_source(key="gemeindeblatt_pdf")
    prop = make_property(town="Bruckmühl")
    _make_observation(session, property=prop, source=source)
    session.flush()

    rows = coverage_by_municipality(session, since=SINCE, expected=EXPECTED)

    by_town = {row.town: row.observed for row in rows}
    assert by_town["Bruckmühl"] == 1
    assert by_town["Weyarn"] == 0
    assert by_town["Holzkirchen"] == 0


def test_every_expected_municipality_appears_even_with_no_data(session) -> None:
    rows = coverage_by_municipality(session, since=SINCE, expected=EXPECTED)

    assert [row.town for row in rows] == EXPECTED
    assert all(row.observed == 0 for row in rows)


def _report_data(**overrides) -> ReportData:
    """A minimal ``ReportData`` for exercising the renderers directly.

    The section under test depends only on ``municipality_coverage`` and
    ``yield_window_weeks`` - not on the shortlist or the counts table - so this
    avoids dragging in the whole ``build_report`` pipeline (profile, scoring,
    a populated database) just to pin two lines of template output.
    """
    now = datetime.now(UTC)
    defaults = dict(
        week_label="KW 36",
        generated_at=now,
        period_start=now.date(),
        since=now - timedelta(days=7),
        profile_name="default",
        profile_hash="deadbeef",
        radius_air_km=80.0,
        radius_driving_soft_km=100.0,
        radius_driving_hard_km=116.0,
        budget_total_max=1_200_000.0,
        budget_purchase_target=750_000.0,
        budget_purchase_hard=900_000.0,
        counts=ReportCounts(),
    )
    defaults.update(overrides)
    return ReportData(**defaults)


def test_dark_municipality_section_lists_zero_observation_towns_only() -> None:
    """A town that produced nothing is named; a town that produced something is not.

    Pinned per reviewer note on the yield table: this section must not be
    deletable with the whole suite still green.
    """
    data = _report_data(
        municipality_coverage=[
            MunicipalityCoverage(town="Bruckmühl", observed=1),
            MunicipalityCoverage(town="Weyarn", observed=0),
        ]
    )
    heading = f"## Dunkle Gemeinden (letzte {data.yield_window_weeks} Wochen ohne einen einzigen Treffer)"
    closing = "Eine Gemeinde ohne Treffer ist keine ruhige Gemeinde — sie ist eine ungedeckte."

    markdown = render_markdown(data)
    assert heading in markdown
    assert "Weyarn" in markdown
    assert "Bruckmühl" not in markdown
    assert closing in markdown

    html = render_html(data)
    assert "Dunkle Gemeinden" in html
    assert "Weyarn" in html
    assert "Bruckmühl" not in html
    assert closing in html
