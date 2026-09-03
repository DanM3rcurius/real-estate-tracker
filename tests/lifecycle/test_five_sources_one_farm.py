"""One farm, five portals, one Property.

This is the second unacceptable failure mode: the same Hofstelle advertised by
a broker, two portals, the Gemeindeblatt and an aggregator must not become five
properties. Each source describes it slightly differently - that is the point.
"""

from __future__ import annotations

from hofradar.db.enums import ChangeKind, ListingStatus, SourceRole, VerificationStatus
from hofradar.db.models import Observation, Property, PropertySource
from hofradar.lifecycle import ingest

# (key, role, reliability, title, land, living, price)
SOURCES = [
    ("makler-huber", SourceRole.PRIMARY, 0.95, "Hofstelle in Vogtareuth", 8500, 220, 790_000),
    ("portal-immo", SourceRole.PRIMARY, 0.80, "Bauernhaus Vogtareuth", 8620, 218, 790_000),
    ("gemeindeblatt", SourceRole.LOCAL, 0.60, "Anwesen bei Vogtareuth", 8500, 220, 789_000),
    ("kleinanzeigen", SourceRole.PRIMARY, 0.55, "Sacherl bei Vogtareuth", 8400, 224, 795_000),
    ("aggregator", SourceRole.DISCOVERY, 0.30, "Hofstelle Vogtareuth", 8500, 215, 790_000),
]


def test_five_listings_collapse_into_one_property(
    db_session, make_source, make_listing, make_geo
):
    geo = make_geo(47.9412, 12.1988, precision="exact")

    kinds = []
    prop = None
    for index, (key, role, reliability, title, land, living, price) in enumerate(SOURCES):
        source = make_source(key, role=role, reliability=reliability)
        listing = make_listing(
            source_key=key,
            url=f"https://{key}.example/objekt/{index}",
            title=title,
            land_sqm=land,
            living_sqm=living,
            price=price,
            year_built=1890,
            town="Vogtareuth",
            postcode="83569",
            external_id=f"{key}-{index}",
        )
        prop, change = ingest(db_session, listing, source=source, geo=geo, run_id=1)
        kinds.append(change.kind)

    assert db_session.query(Property).count() == 1
    assert db_session.query(PropertySource).filter_by(property_id=prop.id).count() == 5
    assert db_session.query(Observation).filter_by(property_id=prop.id).count() == 5

    assert kinds[0] == ChangeKind.FIRST_SEEN
    assert all(k != ChangeKind.FIRST_SEEN for k in kinds[1:])
    assert prop.source_count == 5


def test_the_best_source_is_the_most_reliable_primary(
    db_session, make_source, make_listing, make_geo
):
    geo = make_geo(47.9412, 12.1988)
    prop = None
    for index, (key, role, reliability, title, land, living, price) in enumerate(SOURCES):
        source = make_source(key, role=role, reliability=reliability)
        prop, _ = ingest(
            db_session,
            make_listing(
                source_key=key,
                url=f"https://{key}.example/objekt/{index}",
                title=title,
                land_sqm=land,
                living_sqm=living,
                price=price,
                year_built=1890,
            ),
            source=source,
            geo=geo,
        )

    best = [ps for ps in prop.property_sources if ps.is_best]
    assert len(best) == 1
    assert best[0].source.key == "makler-huber"
    assert prop.listing_status == ListingStatus.ACTIVE
    assert prop.verification_status == VerificationStatus.VERIFIED


def test_the_canonical_facts_come_from_the_verifying_sources(
    db_session, make_source, make_listing, make_geo
):
    """The aggregator crawls last with a different living area; it must not win."""
    geo = make_geo(47.9412, 12.1988)
    prop = None
    for index, (key, role, reliability, title, land, living, price) in enumerate(SOURCES):
        source = make_source(key, role=role, reliability=reliability)
        prop, _ = ingest(
            db_session,
            make_listing(
                source_key=key,
                url=f"https://{key}.example/objekt/{index}",
                title=title,
                land_sqm=land,
                living_sqm=living,
                price=price,
                year_built=1890,
            ),
            source=source,
            geo=geo,
        )

    # kleinanzeigen (a primary) was the last source allowed to overwrite.
    assert prop.living_sqm == 224
    assert prop.price == 795_000
