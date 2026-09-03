"""Ageing out, and reading the change log back for the weekly report."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hofradar.db.enums import ChangeKind, ListingStatus, SourceRole
from hofradar.db.models import utcnow
from hofradar.lifecycle import apply_stale_rules, changes_since, ingest, mark_missing


def test_an_unseen_property_goes_stale_not_removed(db_session, make_source, make_listing):
    """We stopped hearing about it. That is not proof that it is gone."""
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)
    prop, _ = ingest(
        db_session,
        make_listing(source_key=source.key, land_sqm=8500, price=790_000),
        source=source,
    )
    prop.last_seen = utcnow() - timedelta(days=60)
    db_session.flush()

    changes = apply_stale_rules(db_session, stale_after_days=45, run_id=9)

    assert [c.kind for c in changes] == [ChangeKind.STALE]
    assert prop.listing_status == ListingStatus.STALE
    assert prop.removed_at is None


def test_a_recently_seen_property_is_left_alone(db_session, make_source, make_listing):
    source = make_source("bauernhoefe")
    prop, _ = ingest(
        db_session,
        make_listing(source_key=source.key, land_sqm=8500, price=790_000),
        source=source,
    )
    prop.last_seen = utcnow() - timedelta(days=10)
    db_session.flush()

    assert apply_stale_rules(db_session, stale_after_days=45) == []
    assert prop.listing_status == ListingStatus.ACTIVE


def test_a_stale_property_that_returns_is_reactivated(db_session, make_source, make_listing):
    source = make_source("bauernhoefe")
    listing = make_listing(
        source_key=source.key, url="https://x.example/1", land_sqm=8500, price=790_000
    )
    prop, _ = ingest(db_session, listing, source=source, run_id=1)
    prop.last_seen = utcnow() - timedelta(days=60)
    db_session.flush()
    apply_stale_rules(db_session, stale_after_days=45, run_id=2)

    _, change = ingest(db_session, listing, source=source, run_id=3)

    assert change.kind == ChangeKind.REACTIVATED
    assert change.kind != ChangeKind.FIRST_SEEN
    assert prop.listing_status == ListingStatus.ACTIVE


def test_changes_since_returns_report_ready_dicts(db_session, make_source, make_listing):
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)
    facts = dict(source_key=source.key, url="https://x.example/1", land_sqm=8500, living_sqm=220)
    since = datetime(2000, 1, 1, tzinfo=UTC)

    prop, _ = ingest(db_session, make_listing(price=790_000, **facts), source=source, run_id=1)
    ingest(db_session, make_listing(price=749_000, **facts), source=source, run_id=2)
    mark_missing(db_session, set(), source=source, run_id=3, enumeration_complete=True)

    entries = changes_since(db_session, since)
    kinds = [e["kind"] for e in entries]

    assert set(kinds) == {ChangeKind.FIRST_SEEN, ChangeKind.PRICE_CHANGE, ChangeKind.REMOVED}
    first = entries[0]
    assert first["public_id"] == prop.public_id
    assert first["url"] == "https://x.example/1"
    assert first["town"] == "Vogtareuth"
    assert first["source_count"] == 1

    price_entries = changes_since(db_session, since, kinds=[ChangeKind.PRICE_CHANGE])
    assert len(price_entries) == 1
    assert price_entries[0]["old_price"] == 790_000
    assert price_entries[0]["new_price"] == 749_000
    assert round(price_entries[0]["delta_pct"], 2) == -5.19


def test_changes_since_hides_merged_away_rows(db_session, make_source, make_listing, make_geo):
    """A property merged into another must never appear twice in the report."""
    from hofradar.dedupe import merge_properties

    a = make_source("portal-a", role=SourceRole.PRIMARY)
    b = make_source("portal-b", role=SourceRole.PRIMARY)
    keep, _ = ingest(
        db_session,
        make_listing(source_key=a.key, url="https://a.example/1", land_sqm=8500, price=790_000),
        source=a,
    )
    drop, _ = ingest(
        db_session,
        make_listing(
            source_key=b.key, url="https://b.example/2", town="Passau", postcode="94032",
            land_sqm=2100, price=310_000,
        ),
        source=b,
    )
    assert keep.id != drop.id

    merge_properties(db_session, keep, drop)

    entries = changes_since(db_session, datetime(2000, 1, 1, tzinfo=UTC))
    assert {e["property_id"] for e in entries} == {keep.id}
    assert len(entries) == 2  # both FIRST_SEEN rows now belong to the survivor
