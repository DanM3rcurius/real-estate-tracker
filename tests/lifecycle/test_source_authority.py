"""What a discovery source is and is not allowed to prove.

An aggregator, a cached search result or a web archive may *find* a farmstead.
It may not claim the listing is live, may not date it, and its silence may not
remove it.
"""

from __future__ import annotations

from hofradar.db.enums import ChangeKind, ListingStatus, SourceRole, VerificationStatus
from hofradar.db.models import VerificationEvent, utcnow
from hofradar.lifecycle import ingest, mark_missing


def test_a_discovery_source_cannot_verify(db_session, make_source, make_listing):
    aggregator = make_source("aggregator", role=SourceRole.DISCOVERY, reliability=0.3)
    listing = make_listing(
        source_key=aggregator.key,
        land_sqm=8500,
        living_sqm=220,
        price=790_000,
        source_date=utcnow(),
    )

    prop, change = ingest(db_session, listing, source=aggregator, run_id=1)

    assert change.kind == ChangeKind.FIRST_SEEN
    assert prop.listing_status == ListingStatus.DISCOVERED
    assert prop.verification_status == VerificationStatus.UNVERIFIED
    assert prop.last_verified is None
    # An aggregator's crawl date is not freshness evidence.
    assert prop.source_date is None
    assert db_session.query(VerificationEvent).count() == 0


def test_a_discovery_source_may_only_fill_a_hole(db_session, make_source, make_listing):
    primary = make_source("portal", role=SourceRole.PRIMARY, reliability=0.9)
    aggregator = make_source("aggregator", role=SourceRole.DISCOVERY, reliability=0.3)

    prop, _ = ingest(
        db_session,
        make_listing(
            source_key=primary.key,
            url="https://portal.example/1",
            land_sqm=8500,
            living_sqm=220,
            price=790_000,
            year_built=1890,
            description="Original vom Makler.",
        ),
        source=primary,
    )

    ingest(
        db_session,
        make_listing(
            source_key=aggregator.key,
            url="https://aggregator.example/2",
            land_sqm=8500,
            living_sqm=220,
            price=649_000,               # a stale cached price: must not win
            year_built=1890,
            rooms=8,                     # unknown so far: may be filled in
            description="Kopie aus dem Cache.",
        ),
        source=aggregator,
    )

    assert prop.price == 790_000
    assert prop.description == "Original vom Makler."
    assert prop.rooms == 8
    assert prop.listing_status == ListingStatus.ACTIVE  # unchanged by discovery


def test_a_discovery_sources_silence_removes_nothing(db_session, make_source, make_listing):
    aggregator = make_source("aggregator", role=SourceRole.DISCOVERY)
    prop, _ = ingest(
        db_session,
        make_listing(source_key=aggregator.key, land_sqm=8500, price=790_000),
        source=aggregator,
    )

    assert mark_missing(db_session, set(), source=aggregator, run_id=2, enumeration_complete=True) == []
    assert prop.listing_status == ListingStatus.DISCOVERED
    assert prop.removed_at is None


def test_a_discovery_source_reopens_a_removed_property_without_verifying_it(
    db_session, make_source, make_listing
):
    primary = make_source("portal", role=SourceRole.PRIMARY)
    aggregator = make_source("aggregator", role=SourceRole.DISCOVERY)
    shared = dict(land_sqm=8500, living_sqm=220, price=790_000, year_built=1890)

    prop, _ = ingest(
        db_session,
        make_listing(source_key=primary.key, url="https://portal.example/1", **shared),
        source=primary,
    )
    # A second, still-visible listing on `primary`: mark_missing's empty-
    # seen-set guard is unconditional, so an honest "only this one is gone"
    # needs a real second listing rather than an empty set (Task 3, review
    # round 1, Important 1).
    anchor, _ = ingest(
        db_session,
        make_listing(
            source_key=primary.key, url="https://portal.example/anchor",
            town="Bad Aibling", postcode="83043", land_sqm=1200, living_sqm=90,
            price=250_000, year_built=1950,
        ),
        source=primary,
    )
    mark_missing(db_session, {anchor.id}, source=primary, run_id=2, enumeration_complete=True)
    assert prop.listing_status == ListingStatus.REMOVED

    _, change = ingest(
        db_session,
        make_listing(source_key=aggregator.key, url="https://aggregator.example/2", **shared),
        source=aggregator,
        run_id=3,
    )

    assert change.kind == ChangeKind.REACTIVATED
    assert change.kind != ChangeKind.FIRST_SEEN
    # Back in the funnel, but nobody has proven it is live again.
    assert prop.listing_status == ListingStatus.DISCOVERED
