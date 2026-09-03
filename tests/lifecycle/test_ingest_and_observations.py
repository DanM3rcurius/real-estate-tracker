"""Ingest basics, and the two invariants everything else rests on.

* ``Observation`` is append-only: N ingests produce N rows and update none.
* ``FIRST_SEEN`` is impossible for a property that already exists.
"""

from __future__ import annotations

from hofradar.db.enums import ChangeKind, ListingStatus, SourceRole, VerificationStatus
from hofradar.db.models import Observation, Property, PropertySource, StatusHistory
from hofradar.lifecycle import ingest


def test_first_ingest_creates_one_property(db_session, make_source, make_listing):
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)
    listing = make_listing(
        source_key=source.key, land_sqm=8500, living_sqm=220, price=790_000, year_built=1890
    )

    prop, change = ingest(db_session, listing, source=source, run_id=1)

    assert change.kind == ChangeKind.FIRST_SEEN
    assert prop.id is not None
    assert prop.public_id
    assert prop.listing_status == ListingStatus.ACTIVE
    assert prop.verification_status == VerificationStatus.VERIFIED
    assert prop.land_sqm == 8500
    assert prop.price_first == 790_000
    assert db_session.query(Property).count() == 1
    assert db_session.query(PropertySource).count() == 1


def test_observations_are_append_only(db_session, make_source, make_listing):
    """Five crawls of one unchanged listing: five observations, one property."""
    source = make_source("bauernhoefe")
    listing = make_listing(
        source_key=source.key, land_sqm=8500, living_sqm=220, price=790_000, year_built=1890
    )

    kinds = []
    for run in range(1, 6):
        _, change = ingest(db_session, listing, source=source, run_id=run)
        kinds.append(change.kind)

    assert db_session.query(Property).count() == 1
    assert db_session.query(Observation).count() == 5
    assert kinds[0] == ChangeKind.FIRST_SEEN
    assert all(k != ChangeKind.FIRST_SEEN for k in kinds[1:])
    assert kinds[1:] == [ChangeKind.UNCHANGED] * 4


def test_never_first_seen_for_a_known_property_whatever_the_status(
    db_session, make_source, make_listing
):
    """Walk the property through every status and re-ingest from each one."""
    source = make_source("bauernhoefe")
    listing = make_listing(
        source_key=source.key, land_sqm=8500, living_sqm=220, price=790_000, year_built=1890
    )
    prop, first = ingest(db_session, listing, source=source, run_id=1)
    assert first.kind == ChangeKind.FIRST_SEEN

    for status in ListingStatus:
        prop.listing_status = status
        db_session.flush()
        _, change = ingest(db_session, listing, source=source, run_id=2)
        assert change.kind != ChangeKind.FIRST_SEEN, status
        assert db_session.query(Property).count() == 1


def test_first_seen_writes_a_status_history_row(db_session, make_source, make_listing):
    source = make_source("bauernhoefe")
    prop, _ = ingest(db_session, make_listing(source_key=source.key), source=source, run_id=7)

    rows = db_session.query(StatusHistory).filter_by(property_id=prop.id).all()
    assert len(rows) == 1
    assert rows[0].change_kind == ChangeKind.FIRST_SEEN
    assert rows[0].old_status is None
    assert rows[0].run_id == 7


def test_a_second_source_is_reported_as_a_source_change(db_session, make_source, make_listing):
    primary = make_source("bauernhoefe", role=SourceRole.PRIMARY, reliability=0.9)
    other = make_source("regional-blatt", role=SourceRole.LOCAL, reliability=0.6)
    facts = dict(land_sqm=8500, living_sqm=220, price=790_000, year_built=1890)

    prop, _ = ingest(
        db_session,
        make_listing(source_key=primary.key, url="https://a.example/1", **facts),
        source=primary,
    )
    same, change = ingest(
        db_session,
        make_listing(source_key=other.key, url="https://b.example/2", **facts),
        source=other,
    )

    assert same.id == prop.id
    assert change.kind == ChangeKind.SOURCE_CHANGE
    assert same.source_count == 2
