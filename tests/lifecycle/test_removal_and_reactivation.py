"""Blueprint Test 4 - a listing disappears and later comes back.

The unacceptable outcome is that the returning farm is reported as NEW. It is
REACTIVATED, and if its price moved on the same run it is PRICE_CHANGE with the
reactivation still journalled.
"""

from __future__ import annotations

import pytest

from hofradar.db.enums import ChangeKind, ListingStatus, SourceRole
from hofradar.db.models import Property, StatusHistory, VerificationEvent
from hofradar.lifecycle import ingest, mark_missing


def _facts(source, url="https://bauernhoefe.example/objekt/4711"):
    return dict(
        source_key=source.key, url=url, land_sqm=8500, living_sqm=220, year_built=1890
    )


def test_a_listing_that_disappears_is_removed_then_reactivated(
    db_session, make_source, make_listing
):
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)
    listing = make_listing(price=790_000, **_facts(source))

    prop, first = ingest(db_session, listing, source=source, run_id=1)
    assert first.kind == ChangeKind.FIRST_SEEN
    assert prop.listing_status == ListingStatus.ACTIVE

    # run 2: the source no longer returns it at all.
    removals = mark_missing(db_session, set(), source=source, run_id=2)
    assert [c.kind for c in removals] == [ChangeKind.REMOVED]
    assert prop.listing_status == ListingStatus.REMOVED
    assert prop.removed_at is not None

    # run 3: it is back.
    same, change = ingest(db_session, listing, source=source, run_id=3)

    assert same.id == prop.id
    assert change.kind == ChangeKind.REACTIVATED
    assert change.kind != ChangeKind.FIRST_SEEN
    assert change.old_status == ListingStatus.REMOVED
    assert same.listing_status == ListingStatus.ACTIVE
    assert same.removed_at is None
    assert db_session.query(Property).count() == 1


def test_reactivation_with_a_price_cut_reports_price_change_and_journals_both(
    db_session, make_source, make_listing
):
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)

    prop, _ = ingest(
        db_session, make_listing(price=790_000, **_facts(source)), source=source, run_id=1
    )
    mark_missing(db_session, set(), source=source, run_id=2)
    assert prop.listing_status == ListingStatus.REMOVED

    same, change = ingest(
        db_session, make_listing(price=749_000, **_facts(source)), source=source, run_id=3
    )

    assert change.kind == ChangeKind.PRICE_CHANGE
    assert change.delta_pct == pytest.approx(-5.19, abs=0.01)
    assert same.listing_status == ListingStatus.ACTIVE

    kinds = [
        h.change_kind
        for h in db_session.query(StatusHistory)
        .filter_by(property_id=prop.id)
        .order_by(StatusHistory.id)
        .all()
    ]
    assert kinds == [
        ChangeKind.FIRST_SEEN,
        ChangeKind.REMOVED,
        ChangeKind.REACTIVATED,
        ChangeKind.PRICE_CHANGE,
    ]


def test_an_explicit_404_from_a_verifying_source_removes_immediately(
    db_session, make_source, make_listing
):
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)
    prop, _ = ingest(
        db_session, make_listing(price=790_000, **_facts(source)), source=source, run_id=1
    )

    _, change = ingest(
        db_session,
        make_listing(price=790_000, listing_visible=False, http_status=404, **_facts(source)),
        source=source,
        run_id=2,
    )

    assert change.kind == ChangeKind.REMOVED
    assert prop.listing_status == ListingStatus.REMOVED
    outcomes = [
        e.outcome for e in db_session.query(VerificationEvent).filter_by(property_id=prop.id)
    ]
    assert outcomes == ["verified", "gone"]


def test_one_source_dropping_it_is_not_a_removal(db_session, make_source, make_listing):
    """Two verifying sources carry the farm; one stops. It is still live."""
    a = make_source("portal-a", role=SourceRole.PRIMARY, reliability=0.9)
    b = make_source("portal-b", role=SourceRole.LOCAL, reliability=0.6)
    shared = dict(land_sqm=8500, living_sqm=220, price=790_000, year_built=1890)

    prop, _ = ingest(
        db_session,
        make_listing(source_key=a.key, url="https://a.example/1", **shared),
        source=a,
    )
    ingest(
        db_session,
        make_listing(source_key=b.key, url="https://b.example/2", **shared),
        source=b,
    )

    changes = mark_missing(db_session, set(), source=a, run_id=2)

    assert changes == []
    assert prop.listing_status == ListingStatus.ACTIVE

    changes = mark_missing(db_session, set(), source=b, run_id=3)
    assert [c.kind for c in changes] == [ChangeKind.REMOVED]
    assert prop.listing_status == ListingStatus.REMOVED


def test_properties_still_seen_this_run_are_untouched(db_session, make_source, make_listing):
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)
    prop, _ = ingest(
        db_session, make_listing(price=790_000, **_facts(source)), source=source, run_id=1
    )

    assert mark_missing(db_session, {prop.id}, source=source, run_id=2) == []
    assert prop.listing_status == ListingStatus.ACTIVE


def test_a_still_missing_property_is_not_reported_as_reactivated(
    db_session, make_source, make_listing
):
    """Re-checking a removed listing week after week must stay quiet."""
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)
    gone = make_listing(price=790_000, listing_visible=False, http_status=410, **_facts(source))

    ingest(db_session, make_listing(price=790_000, **_facts(source)), source=source, run_id=1)
    _, removed = ingest(db_session, gone, source=source, run_id=2)
    _, again = ingest(db_session, gone, source=source, run_id=3)

    assert removed.kind == ChangeKind.REMOVED
    assert again.kind == ChangeKind.UNCHANGED
