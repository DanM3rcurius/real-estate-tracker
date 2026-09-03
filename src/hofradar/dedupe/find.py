"""Candidate retrieval: which stored properties is this listing worth comparing to?

``compare`` is expensive-ish and, more importantly, *quadratic* if handed the
whole table. Every week the pipeline re-ingests thousands of listings against a
growing property table, so the candidate set has to come from indexed columns
only. Four blocking passes are tried, cheapest and most selective first, and
their results unioned:

1. the exact listing again - same source and same URL, or same source and same
   ``external_id`` (both covered by indexes on ``property_sources``);
2. the coarse :func:`~hofradar.dedupe.fingerprint.fingerprint`
   (``properties.fingerprint`` is indexed);
3. postcode or town (both indexed);
4. a latitude/longitude bounding box (``ix_properties_geo``).

Every query is bounded by :data:`CANDIDATE_LIMIT`, so a pathological town never
turns into a table scan.
"""

from __future__ import annotations

import math

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from hofradar.contracts import DuplicateVerdict, NormalizedListing
from hofradar.db.models import Property, PropertySource, Source
from hofradar.dedupe._facts import GeoLike, facts_of
from hofradar.dedupe.compare import compare_facts
from hofradar.dedupe.fingerprint import fingerprint

#: Upper bound on candidates fetched per blocking pass.
CANDIDATE_LIMIT = 200
#: Half-width of the geographic blocking box.
GEO_BLOCK_KM = 2.0

_KM_PER_DEGREE_LAT = 111.32


def find_duplicate(
    session: Session,
    listing: NormalizedListing,
    *,
    lat: float | None = None,
    lon: float | None = None,
    geo: GeoLike = None,
) -> DuplicateVerdict:
    """Return the best duplicate verdict for ``listing`` against stored properties.

    ``lat``/``lon`` are the documented contract; ``geo`` additionally accepts a
    ``GeoResult`` so the caller can pass geocode *precision* along, which the
    150 m rule needs in order not to treat a shared town centroid as proof.
    """
    if geo is None and (lat is not None or lon is not None):
        geo = (lat, lon)
    facts = facts_of(listing, geo=geo)

    exact = _exact_source_match(session, listing)
    if exact is not None:
        prop, why = exact
        return DuplicateVerdict(
            is_duplicate=True,
            confidence=1.0,
            reasons=[f"same_listing: {why} (proof)"],
            matched_property_id=prop.id,
        )

    candidates = _candidates(session, listing, facts.lat, facts.lon)
    if not candidates:
        return DuplicateVerdict(
            is_duplicate=False,
            confidence=0.0,
            reasons=["no_candidates: nothing in the database blocks with this listing"],
            matched_property_id=None,
        )

    best: DuplicateVerdict | None = None
    for prop in candidates:
        verdict = compare_facts(facts, facts_of(prop))
        if best is None:
            best = verdict
            continue
        if (verdict.is_duplicate, verdict.confidence) > (best.is_duplicate, best.confidence):
            best = verdict
    assert best is not None
    return best


def _exact_source_match(
    session: Session, listing: NormalizedListing
) -> tuple[Property, str] | None:
    """The same URL, or the same external id, on the same source.

    This is proof, not evidence: it is literally the listing we already have a
    row for. It short-circuits the similarity model, which matters because a
    thin listing (no price, no areas) would otherwise fail to score highly
    enough against the property it created last week and would be inserted a
    second time.
    """
    if not listing.source_key:
        return None
    filters = []
    if listing.url:
        filters.append((PropertySource.url == listing.url, f"{listing.source_key} {listing.url}"))
    if listing.external_id:
        filters.append(
            (
                PropertySource.external_id == str(listing.external_id),
                f"{listing.source_key} external_id={listing.external_id}",
            )
        )
    for condition, why in filters:
        prop = session.execute(
            select(Property)
            .join(PropertySource, PropertySource.property_id == Property.id)
            .join(Source, Source.id == PropertySource.source_id)
            .where(Source.key == listing.source_key, condition,
                   Property.merged_into_id.is_(None))
            .limit(1)
        ).scalars().first()
        if prop is not None:
            return prop, why
    return None


def _candidates(
    session: Session,
    listing: NormalizedListing,
    lat: float | None,
    lon: float | None,
) -> list[Property]:
    """Union the blocking passes, de-duplicated by property id, order stable."""
    found: dict[int, Property] = {}

    for stmt in _blocking_statements(session, listing, lat, lon):
        for prop in session.execute(stmt).scalars():
            if prop.merged_into_id is None:
                found.setdefault(prop.id, prop)
        if len(found) >= CANDIDATE_LIMIT:
            break

    return list(found.values())[:CANDIDATE_LIMIT]


def _blocking_statements(
    session: Session,
    listing: NormalizedListing,
    lat: float | None,
    lon: float | None,
):
    alive = Property.merged_into_id.is_(None)

    # 1. the very same listing again, via property_sources.
    same_listing_filters = []
    if listing.url:
        same_listing_filters.append(PropertySource.url == listing.url)
    if listing.external_id:
        same_listing_filters.append(PropertySource.external_id == str(listing.external_id))
    if same_listing_filters:
        yield (
            select(Property)
            .join(PropertySource, PropertySource.property_id == Property.id)
            .join(Source, Source.id == PropertySource.source_id)
            .where(Source.key == listing.source_key, or_(*same_listing_filters), alive)
            .limit(CANDIDATE_LIMIT)
        )

    # 2. the coarse fingerprint bucket.
    yield (
        select(Property)
        .where(Property.fingerprint == fingerprint(listing, geo=(lat, lon)), alive)
        .limit(CANDIDATE_LIMIT)
    )

    # 3. postcode / town.
    location_filters = []
    if listing.postcode:
        location_filters.append(Property.postcode == listing.postcode)
    if listing.town:
        location_filters.append(Property.town == listing.town)
    if location_filters:
        yield select(Property).where(or_(*location_filters), alive).limit(CANDIDATE_LIMIT)

    # 4. geographic bounding box.
    box = _bounding_box(lat, lon)
    if box is not None:
        min_lat, max_lat, min_lon, max_lon = box
        yield (
            select(Property)
            .where(
                Property.lat.between(min_lat, max_lat),
                Property.lon.between(min_lon, max_lon),
                alive,
            )
            .limit(CANDIDATE_LIMIT)
        )


def _bounding_box(
    lat: float | None, lon: float | None
) -> tuple[float, float, float, float] | None:
    if lat is None or lon is None:
        return None
    d_lat = GEO_BLOCK_KM / _KM_PER_DEGREE_LAT
    cos_lat = max(0.01, math.cos(math.radians(lat)))
    d_lon = GEO_BLOCK_KM / (_KM_PER_DEGREE_LAT * cos_lat)
    return (lat - d_lat, lat + d_lat, lon - d_lon, lon + d_lon)
