"""The digest, and its one absolute rule.

A property the database has ever seen is never announced as NEU. The rule is
tested against the case that actually breaks naive implementations: a row whose
``first_seen`` looks recent but whose observations reach back months, which is
what a merge, a re-ingest or a back-fill produces.
"""

from __future__ import annotations

from datetime import timedelta

from hofradar.db.enums import ChangeKind, ListingStatus
from hofradar.report import build_report, render_html, render_markdown, save_report
from tests.web.conftest import (
    NOW,
    add_observation,
    add_price_change,
    add_source_link,
    add_status_change,
    make_property,
)


def since_week():
    return NOW - timedelta(days=7)


def test_report_lists_a_genuinely_new_property_as_new(db, source, default_profile):
    prop = make_property(
        db,
        public_id="HF-NEW",
        first_seen=NOW - timedelta(days=2),
        last_seen=NOW,
    )
    add_source_link(db, prop, source)
    add_observation(db, prop, source, at=NOW - timedelta(days=2))

    data = build_report(db, default_profile, since=since_week(), now=NOW)
    entry = next(e for e in data.entries if e.public_id == "HF-NEW")
    assert entry.category == "new"
    assert data.counts.new_candidates == 1


def test_previously_known_property_is_never_new(db, source, default_profile):
    """``first_seen`` says two days; the observations say six months."""
    prop = make_property(
        db,
        public_id="HF-OLD",
        first_seen=NOW - timedelta(days=2),
        last_seen=NOW,
    )
    add_source_link(db, prop, source)
    add_observation(db, prop, source, at=NOW - timedelta(days=180))
    add_observation(db, prop, source, at=NOW - timedelta(days=1))
    add_price_change(db, prop, old=690_000.0, new=595_000.0, at=NOW - timedelta(days=1))

    data = build_report(db, default_profile, since=since_week(), now=NOW)

    entry = next(e for e in data.entries if e.public_id == "HF-OLD")
    assert entry.category != "new"
    assert entry.category == "price_change"
    assert data.counts.new_candidates == 0
    assert data.counts.price_changes == 1

    markdown = render_markdown(data)
    assert "🆕 NEU" not in markdown
    assert "🔻 PREISÄNDERUNG" in markdown


def test_reactivated_property_is_reactivated_not_new(db, source, default_profile):
    prop = make_property(
        db,
        public_id="HF-BACK",
        first_seen=NOW - timedelta(days=300),
        listing_status=ListingStatus.ACTIVE,
    )
    add_source_link(db, prop, source)
    add_observation(db, prop, source, at=NOW - timedelta(days=300))
    add_status_change(
        db,
        prop,
        old=ListingStatus.REMOVED,
        new=ListingStatus.ACTIVE,
        at=NOW - timedelta(days=1),
        kind=ChangeKind.REACTIVATED,
    )

    data = build_report(db, default_profile, since=since_week(), now=NOW)
    entry = next(e for e in data.entries if e.public_id == "HF-BACK")
    assert entry.category == "reactivated"
    assert data.counts.new_candidates == 0
    assert data.counts.reactivated == 1


def test_no_entry_in_any_report_claims_new_without_earning_it(db, source, seeded, default_profile):
    """The enforcement sweep, exercised against the whole seeded fixture."""
    from hofradar.web.history import is_genuinely_new

    data = build_report(db, default_profile, since=since_week(), now=NOW)
    by_id = {p.public_id: p for p in seeded.values()}
    for entry in data.entries:
        if entry.category == "new":
            assert is_genuinely_new(by_id[entry.public_id], since_week())


def test_report_counts_rather_than_lists(db, source, default_profile):
    for index in range(25):
        prop = make_property(
            db,
            public_id=f"HF-{index:04d}",
            first_seen=NOW - timedelta(days=2 + index),
        )
        add_source_link(db, prop, source)
        add_observation(db, prop, source, at=NOW - timedelta(days=2 + index))

    data = build_report(db, default_profile, since=since_week(), now=NOW)
    assert len(data.entries) <= default_profile.gates.shortlist_size
    assert data.counts.tracked_total == 25
    assert data.counts.not_listed == 25 - len(data.entries)

    markdown = render_markdown(data)
    assert "Nicht einzeln gelistet" in markdown


def test_report_header_carries_the_sliders(db, default_profile):
    data = build_report(db, default_profile, since=since_week(), now=NOW)
    markdown = render_markdown(data)
    assert data.week_label.startswith("KW ")
    assert "Entfernung" in markdown
    assert "Gesamtbudget" in markdown
    assert data.profile_hash == default_profile.profile_hash


def test_render_html_is_an_embeddable_fragment(db, seeded, default_profile):
    data = build_report(db, default_profile, since=since_week(), now=NOW)
    html = render_html(data)
    assert html.lstrip().startswith("<article")
    assert "<html" not in html
    assert data.week_label in html


def test_report_shows_source_yield_with_in_radius_as_its_own_column(db, source, default_profile):
    """Both renderers must carry the yield section, and never fold in_radius into observed.

    A source that parses perfectly and yields nothing inside the radius is a
    source that should not have been built - see hofradar.report.yield_stats.
    If someone deletes the "Quellen-Ausbeute" section or collapses in_radius
    into the total, that regression must fail a test, not just a hand-check.
    """
    near = make_property(db, public_id="HF-YIELD-NEAR", distance_air_km=30.0)
    far = make_property(db, public_id="HF-YIELD-FAR", distance_air_km=250.0)
    add_observation(db, near, source, at=NOW - timedelta(days=1))
    add_observation(db, far, source, at=NOW - timedelta(days=1))

    data = build_report(db, default_profile, since=since_week(), now=NOW)
    row = next(r for r in data.source_yields if r.source_key == source.key)
    assert row.observed == 2
    assert row.in_radius == 1  # only the near property is inside the profile's radius

    heading = f"## Quellen-Ausbeute (letzte {data.yield_window_weeks} Wochen)"
    markdown = render_markdown(data)
    assert heading in markdown
    # Both counts must appear as distinct table cells - not one number covering both.
    assert f"| {source.key} | {row.observed} | {row.in_radius} |" in markdown

    html = render_html(data)
    assert f"<h2>Quellen-Ausbeute (letzte {data.yield_window_weeks} Wochen)</h2>" in html
    assert (
        f'<th scope="row">{source.key}</th><td>{row.observed}</td><td>{row.in_radius}</td>'
        in html
    )


def test_report_entry_never_borrows_air_distance_for_driving(db, seeded, default_profile):
    data = build_report(db, default_profile, since=since_week(), now=NOW)
    entry = next(e for e in data.entries if e.public_id == "HF-0001")
    assert entry.distance_driving_km is None
    assert entry.driving_display == "nicht geprüft"
    assert "23,4" not in entry.driving_display


def test_save_report_persists_and_page_shows_it(db, client, seeded, default_profile):
    data = build_report(db, default_profile, since=since_week(), now=NOW)
    record = save_report(db, data)
    assert record.id is not None
    assert record.markdown and record.html
    assert record.summary["counts"]["tracked_total"] == 4

    response = client.get(f"/report/{record.id}")
    assert response.status_code == 200
    assert data.week_label in response.text
    assert "Markdown kopieren" in response.text


def test_report_generation_through_the_web(client, seeded):
    response = client.post("/report")
    assert response.status_code == 200
    assert "Report erzeugt und gespeichert" in response.text


def test_an_archived_property_is_not_in_the_digest(db, seeded, default_profile):
    """Archiving is a reader-facing hide, and the digest is a reader."""
    from sqlalchemy import select

    from hofradar.db.models import Property

    prop = db.scalar(select(Property).where(Property.public_id == "HF-0001"))
    prop.user_state = "archived"
    db.commit()

    data = build_report(db, default_profile, since=since_week(), now=NOW)
    assert "HF-0001" not in [entry.public_id for entry in data.entries]
    # It is hidden, not forgotten: the tracked total still counts it.
    assert data.counts.tracked_total == 4
