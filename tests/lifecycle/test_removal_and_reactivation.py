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


def _anchor(db_session, make_listing, source, *, suffix="anchor",
            town="Bad Aibling", postcode="83043", land_sqm=1200.0):
    """A second, always-seen property on ``source``.

    Exists only so a call site can hand ``mark_missing`` an honestly non-empty
    seen-set: the empty-seen-set guard it now enforces is unconditional (Task
    3, review round 1, Important 1) - a run that saw *nothing at all* is
    always refused, even from a source with only one or two listings. "This
    one listing disappeared, everything else this source carries is still
    there" therefore needs a second, genuinely-still-visible listing to say
    so; ``set()`` can no longer represent it.
    """
    prop, _ = ingest(
        db_session,
        make_listing(
            source_key=source.key,
            url=f"https://bauernhoefe.example/{suffix}",
            town=town,
            postcode=postcode,
            land_sqm=land_sqm,
            living_sqm=90.0,
            price=250_000,
            year_built=1950,
        ),
        source=source,
    )
    return prop


def test_a_listing_that_disappears_is_removed_then_reactivated(
    db_session, make_source, make_listing
):
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)
    listing = make_listing(price=790_000, **_facts(source))

    prop, first = ingest(db_session, listing, source=source, run_id=1)
    assert first.kind == ChangeKind.FIRST_SEEN
    assert prop.listing_status == ListingStatus.ACTIVE

    # A second listing the source still carries, so run 2's seen-set is
    # honestly non-empty - see _anchor's docstring.
    anchor = _anchor(db_session, make_listing, source)

    # run 2: the source no longer returns `prop` - only the anchor.
    removals = mark_missing(
        db_session, {anchor.id}, source=source, run_id=2, enumeration_complete=True
    )
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
    # prop (reactivated in place, not recreated) plus the anchor seeded above
    # to give mark_missing a non-empty seen-set - still exactly two, not three.
    assert db_session.query(Property).count() == 2


def test_reactivation_with_a_price_cut_reports_price_change_and_journals_both(
    db_session, make_source, make_listing
):
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)

    prop, _ = ingest(
        db_session, make_listing(price=790_000, **_facts(source)), source=source, run_id=1
    )
    anchor = _anchor(db_session, make_listing, source)
    mark_missing(db_session, {anchor.id}, source=source, run_id=2, enumeration_complete=True)
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

    anchor_a = _anchor(
        db_session, make_listing, a, suffix="anchor-a", town="Bad Aibling", postcode="83043",
        land_sqm=1200.0,
    )
    changes = mark_missing(
        db_session, {anchor_a.id}, source=a, run_id=2, enumeration_complete=True
    )

    assert changes == []
    assert prop.listing_status == ListingStatus.ACTIVE

    anchor_b = _anchor(
        db_session, make_listing, b, suffix="anchor-b", town="Wasserburg am Inn",
        postcode="83512", land_sqm=900.0,
    )
    changes = mark_missing(
        db_session, {anchor_b.id}, source=b, run_id=3, enumeration_complete=True
    )
    assert [c.kind for c in changes] == [ChangeKind.REMOVED]
    assert prop.listing_status == ListingStatus.REMOVED


def test_properties_still_seen_this_run_are_untouched(db_session, make_source, make_listing):
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)
    prop, _ = ingest(
        db_session, make_listing(price=790_000, **_facts(source)), source=source, run_id=1
    )

    assert mark_missing(db_session, {prop.id}, source=source, run_id=2, enumeration_complete=True) == []
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
