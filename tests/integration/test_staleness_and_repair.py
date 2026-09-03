"""Two clocks for staleness, and undoing the removals issue #2 already wrote.

STALE means "we stopped hearing about it". That only means something where
there was a stream to fall silent, so a property nothing will ever re-report
must not be aged on the same clock as one a portal mentions every week.
"""

from __future__ import annotations

from datetime import timedelta

from hofradar.db.enums import ChangeKind, ListingStatus, SourceRole
from hofradar.db.models import PropertySource, StatusHistory, utcnow
from hofradar.lifecycle import apply_stale_rules, ingest, repair_phantom_removals
from hofradar.lifecycle.absence import (
    DEFAULT_STALE_AFTER_DAYS,
    DEFAULT_UNVERIFIED_STALE_AFTER_DAYS,
)


def _aged(db_session, make_source, make_listing, *, key: str, days: int):
    source = make_source(key=key, role=SourceRole.PRIMARY)
    listing = make_listing(
        url=f"https://{key}.invalid/objekt-1", title="Hofstelle", town="Vogtareuth",
        postcode="83569", price=495_000.0, land_sqm=2_100.0,
    )
    prop, _ = ingest(db_session, listing, source=source, run_id=1)
    prop.last_seen = utcnow() - timedelta(days=days)
    db_session.flush()
    return source, prop


# --------------------------------------------------------------------------- #
# Two clocks
# --------------------------------------------------------------------------- #


def test_a_self_reporting_source_still_ages_on_the_short_clock(
    db_session, make_source, make_listing
) -> None:
    source, prop = _aged(
        db_session, make_source, make_listing, key="broker",
        days=DEFAULT_STALE_AFTER_DAYS + 1,
    )

    changes = apply_stale_rules(db_session, non_reporting_source_ids=set())

    assert len(changes) == 1
    assert prop.listing_status == ListingStatus.STALE
    assert "not seen since" in changes[0].detail


def test_a_property_nothing_re_reports_is_spared_the_short_clock(
    db_session, make_source, make_listing
) -> None:
    """The reported blind spot: a hand-added listing went STALE at 45 days."""
    source, prop = _aged(
        db_session, make_source, make_listing, key="manual",
        days=DEFAULT_STALE_AFTER_DAYS + 1,
    )

    changes = apply_stale_rules(db_session, non_reporting_source_ids={source.id})

    assert changes == []
    assert prop.listing_status == ListingStatus.ACTIVE


def test_but_it_does_age_out_eventually(db_session, make_source, make_listing) -> None:
    """It is still stale information - just on the longer, honest clock."""
    source, prop = _aged(
        db_session, make_source, make_listing, key="manual",
        days=DEFAULT_UNVERIFIED_STALE_AFTER_DAYS + 1,
    )

    changes = apply_stale_rules(db_session, non_reporting_source_ids={source.id})

    assert len(changes) == 1
    assert prop.listing_status == ListingStatus.STALE
    assert "no source re-checks this listing" in changes[0].detail


def test_the_short_clock_is_not_skipped_in_the_gap_between_the_two(
    db_session, make_source, make_listing
) -> None:
    """Regression: querying on the longer clock hid these entirely."""
    source, prop = _aged(
        db_session, make_source, make_listing, key="broker",
        days=DEFAULT_STALE_AFTER_DAYS + 5,
    )
    other = make_source(key="manual", role=SourceRole.PRIMARY)

    changes = apply_stale_rules(db_session, non_reporting_source_ids={other.id})

    assert len(changes) == 1, "a self-reporting property between the two clocks was missed"
    assert prop.listing_status == ListingStatus.STALE


def test_one_re_reporting_source_is_enough(db_session, make_source, make_listing) -> None:
    """Carried by both a paste and a broker feed: the feed governs."""
    manual, prop = _aged(
        db_session, make_source, make_listing, key="manual",
        days=DEFAULT_STALE_AFTER_DAYS + 1,
    )
    broker = make_source(key="broker", role=SourceRole.PRIMARY)
    db_session.add(
        PropertySource(
            property_id=prop.id, source_id=broker.id,
            url="https://broker.invalid/objekt-1", role=SourceRole.PRIMARY,
        )
    )
    db_session.flush()

    changes = apply_stale_rules(db_session, non_reporting_source_ids={manual.id})

    assert len(changes) == 1
    assert prop.listing_status == ListingStatus.STALE


# --------------------------------------------------------------------------- #
# Repairing what the bug already wrote
# --------------------------------------------------------------------------- #


def _phantom_removed(db_session, make_source, make_listing, *, key: str):
    source = make_source(key=key, role=SourceRole.PRIMARY)
    listing = make_listing(
        url=f"https://{key}.invalid/objekt-1", title="Sacherl", town="Vogtareuth",
        postcode="83569", price=595_000.0, land_sqm=8_000.0,
    )
    prop, _ = ingest(db_session, listing, source=source, run_id=1)
    prop.listing_status = ListingStatus.REMOVED
    prop.removed_at = utcnow()
    db_session.add(
        StatusHistory(
            property_id=prop.id, observed_at=utcnow(),
            old_status=ListingStatus.ACTIVE, new_status=ListingStatus.REMOVED,
            change_kind=ChangeKind.REMOVED,
            detail=f"no verifying source still lists it ({key})",
        )
    )
    for row in db_session.query(PropertySource).filter_by(property_id=prop.id):
        row.last_listing_visible = False
    db_session.flush()
    return source, prop


def test_a_dry_run_reports_without_writing(db_session, make_source, make_listing) -> None:
    source, prop = _phantom_removed(db_session, make_source, make_listing, key="manual")

    report = repair_phantom_removals(
        db_session, non_reporting_source_keys={"manual"}, dry_run=True
    )

    assert report.restored == [prop.public_id]
    assert prop.listing_status == ListingStatus.REMOVED, "dry run must not write"


def test_applying_restores_the_property(db_session, make_source, make_listing) -> None:
    source, prop = _phantom_removed(db_session, make_source, make_listing, key="manual")

    report = repair_phantom_removals(
        db_session, non_reporting_source_keys={"manual"}, dry_run=False
    )

    assert report.restored == [prop.public_id]
    assert prop.listing_status == ListingStatus.ACTIVE
    assert prop.removed_at is None
    row = db_session.query(PropertySource).filter_by(property_id=prop.id).one()
    assert row.last_listing_visible is True
    assert any(h.change_kind == ChangeKind.REACTIVATED for h in prop.status_history)


def test_a_removal_by_an_enumerating_source_is_left_alone(
    db_session, make_source, make_listing
) -> None:
    """Indistinguishable from a real removal, so inventing a resurrection is wrong."""
    source, prop = _phantom_removed(db_session, make_source, make_listing, key="zvg_bayern")

    report = repair_phantom_removals(
        db_session, non_reporting_source_keys={"manual"}, dry_run=False
    )

    assert report.restored == []
    assert report.skipped_ambiguous == [prop.public_id]
    assert prop.listing_status == ListingStatus.REMOVED


def test_a_removal_with_some_other_reason_is_untouched(
    db_session, make_source, make_listing
) -> None:
    source = make_source(key="manual", role=SourceRole.PRIMARY)
    listing = make_listing(url="https://manual.invalid/x", town="Vogtareuth")
    prop, _ = ingest(db_session, listing, source=source, run_id=1)
    prop.listing_status = ListingStatus.REMOVED
    db_session.add(
        StatusHistory(
            property_id=prop.id, observed_at=utcnow(),
            old_status=ListingStatus.ACTIVE, new_status=ListingStatus.REMOVED,
            change_kind=ChangeKind.REMOVED, detail="seller confirmed sold",
        )
    )
    db_session.flush()

    report = repair_phantom_removals(
        db_session, non_reporting_source_keys={"manual"}, dry_run=False
    )

    assert report.restored == []
    assert prop.listing_status == ListingStatus.REMOVED
