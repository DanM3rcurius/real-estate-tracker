"""Candidate blocking and the merge that collapses two rows into one."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hofradar.db.enums import ListingStatus, SourceRole, VerificationStatus
from hofradar.db.models import (
    Image,
    Observation,
    PriceHistory,
    Property,
    PropertySource,
    StatusHistory,
)
from hofradar.dedupe import find_duplicate, fingerprint, merge_properties
from hofradar.dedupe._util import as_utc
from hofradar.dedupe.find import CANDIDATE_LIMIT, _candidates


def test_find_duplicate_on_an_empty_table(db_session, make_listing):
    verdict = find_duplicate(db_session, make_listing())
    assert verdict.is_duplicate is False
    assert verdict.matched_property_id is None
    assert "no_candidates" in " ".join(verdict.reasons)


def test_find_duplicate_matches_a_stored_property(db_session, make_listing, make_property):
    stored = make_property(
        land_sqm=8500, living_sqm=220, price=790_000, year_built=1890
    )
    stored.fingerprint = fingerprint(stored)
    db_session.flush()

    listing = make_listing(land_sqm=8620, living_sqm=214, price=789_000, year_built=1890)
    verdict = find_duplicate(db_session, listing)

    assert verdict.is_duplicate is True
    assert verdict.matched_property_id == stored.id


def test_blocking_ignores_unrelated_rows(db_session, make_listing, make_property):
    """A property that shares no postcode, town, fingerprint or geo cell must
    never even reach ``compare`` - that is what keeps this off a table scan."""
    make_property(town="Vogtareuth", postcode="83569", land_sqm=8500)
    far_away = make_property(town="Passau", postcode="94032", land_sqm=120_000)

    listing = make_listing(town="Vogtareuth", postcode="83569", land_sqm=8500)
    candidates = _candidates(db_session, listing, None, None)

    assert far_away.id not in {c.id for c in candidates}
    assert len(candidates) <= CANDIDATE_LIMIT


def test_blocking_finds_the_same_url_again(db_session, make_listing, make_property, make_source):
    """Even with every number missing, the same URL on the same source blocks."""
    source = make_source("portal")
    prop = make_property(town=None, postcode=None)
    db_session.add(
        PropertySource(
            property_id=prop.id, source_id=source.id, url="https://portal.example/x/1"
        )
    )
    db_session.flush()

    listing = make_listing(
        source_key="portal", url="https://portal.example/x/1", town=None, postcode=None
    )
    assert prop.id in {c.id for c in _candidates(db_session, listing, None, None)}


def test_merged_rows_are_never_offered_as_candidates(db_session, make_listing, make_property):
    keep = make_property(land_sqm=8500, living_sqm=220, price=790_000, year_built=1890)
    drop = make_property(land_sqm=8500, living_sqm=220, price=790_000, year_built=1890)
    drop.merged_into_id = keep.id
    db_session.flush()

    listing = make_listing(land_sqm=8500, living_sqm=220, price=790_000, year_built=1890)
    ids = {c.id for c in _candidates(db_session, listing, None, None)}

    assert keep.id in ids
    assert drop.id not in ids


# --------------------------------------------------------------------------- #
# merge_properties
# --------------------------------------------------------------------------- #


def _populate(db_session, prop: Property, source, *, url: str, when: datetime) -> None:
    db_session.add_all(
        [
            Observation(
                property_id=prop.id, source_id=source.id, url=url, scraped_at=when,
                price=prop.price,
            ),
            PropertySource(
                property_id=prop.id, source_id=source.id, url=url,
                role=source.role, first_seen=when, last_seen=when,
            ),
            PriceHistory(property_id=prop.id, observed_at=when, new_price=prop.price),
            StatusHistory(
                property_id=prop.id, observed_at=when, new_status=prop.listing_status
            ),
            Image(property_id=prop.id, url=f"{url}/photo.jpg", phash="f0e1d2c3b4a59687"),
        ]
    )
    db_session.flush()


def test_merge_moves_all_history_and_never_blanks_a_known_fact(
    db_session, make_property, make_source
):
    early = datetime(2026, 1, 5, tzinfo=UTC)
    late = datetime(2026, 6, 5, tzinfo=UTC)
    primary = make_source("primary-portal", role=SourceRole.PRIMARY, reliability=0.9)
    aggregator = make_source("aggregator", role=SourceRole.DISCOVERY, reliability=0.3)

    keep = make_property(
        canonical_title="Hofstelle in Vogtareuth",
        land_sqm=8500,
        living_sqm=None,
        price=790_000,
        year_built=None,
        first_seen=late,
        last_seen=late,
        building_features=["stadel"],
        evidence={"price": {"source": "aggregator", "confidence": 0.4}},
    )
    drop = make_property(
        canonical_title="Bauernhaus Vogtareuth",
        land_sqm=None,
        living_sqm=220,
        price=None,
        year_built=1890,
        first_seen=early,
        last_seen=early,
        building_features=["obstwiese"],
        is_monument=True,
        evidence={"price": {"source": "primary-portal", "confidence": 0.9}},
    )
    _populate(db_session, keep, primary, url="https://primary-portal.example/a", when=late)
    _populate(db_session, drop, aggregator, url="https://aggregator.example/b", when=early)

    merged = merge_properties(db_session, keep, drop)

    assert merged.id == keep.id
    assert drop.merged_into_id == keep.id

    # history moved, nothing lost
    for model in (Observation, PriceHistory, StatusHistory, PropertySource, Image):
        rows = db_session.query(model).filter(model.property_id == keep.id).all()
        assert len(rows) == 2, model.__name__
        assert db_session.query(model).filter(model.property_id == drop.id).count() == 0

    # facts filled in, never blanked
    assert merged.land_sqm == 8500        # kept, drop had NULL
    assert merged.living_sqm == 220       # filled from drop
    assert merged.price == 790_000
    assert merged.year_built == 1890
    assert merged.is_monument is True     # signal from either side survives

    # tag lists unioned, evidence resolved by confidence
    assert set(merged.building_features) == {"stadel", "obstwiese"}
    assert merged.evidence["price"]["source"] == "primary-portal"

    # earliest first_seen, latest last_seen (SQLite drops the tz on read)
    assert as_utc(merged.first_seen) == early
    assert as_utc(merged.last_seen) == late

    # exactly one best source, and it is the primary one
    best = [ps for ps in merged.property_sources if ps.is_best]
    assert len(best) == 1
    assert best[0].source_id == primary.id


def test_merge_keeps_one_row_per_source_url_and_one_best(db_session, make_property, make_source):
    """The (source, url) unique key is global, so two properties can never hold
    the same source row - merging must still end with one ``is_best``."""
    portal = make_source("portal", role=SourceRole.PRIMARY, reliability=0.9)
    early = datetime(2026, 1, 1, tzinfo=UTC)
    late = early + timedelta(days=90)
    keep = make_property()
    drop = make_property()
    db_session.add_all(
        [
            PropertySource(
                property_id=keep.id, source_id=portal.id,
                url="https://portal.example/objekt/7", role=portal.role,
                first_seen=late, last_seen=late, is_best=True,
            ),
            PropertySource(
                property_id=drop.id, source_id=portal.id,
                url="https://portal.example/objekt/8", role=portal.role,
                external_id="X-9", first_seen=early, last_seen=early, is_best=True,
            ),
        ]
    )
    db_session.flush()

    merged = merge_properties(db_session, keep, drop)

    rows = db_session.query(PropertySource).filter(PropertySource.property_id == merged.id).all()
    assert len(rows) == 2
    assert sum(1 for r in rows if r.is_best) == 1


def test_merge_revives_a_removed_survivor(db_session, make_property):
    keep = make_property(
        listing_status=ListingStatus.REMOVED,
        removed_at=datetime(2026, 2, 1, tzinfo=UTC),
        verification_status=VerificationStatus.UNVERIFIED,
    )
    drop = make_property(
        listing_status=ListingStatus.ACTIVE, verification_status=VerificationStatus.VERIFIED
    )

    merged = merge_properties(db_session, keep, drop)

    assert merged.listing_status == ListingStatus.ACTIVE
    assert merged.removed_at is None
    assert merged.verification_status == VerificationStatus.VERIFIED
