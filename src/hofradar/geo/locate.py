"""Combine geocoding, air distance and (conditionally) road distance into
one :class:`GeoResult` for a listing.

Routing is the expensive, rate-limited call, so it only happens once the
property is already known to be inside the air-distance radius - there is no
reason to ask OSRM about a farm that will be rejected on air distance alone.
If routing then fails, ``routed`` stays ``False`` and the driving fields
stay ``None``: that is a fact ("we don't know"), never silently treated as
"it's fine" or backfilled from the air distance.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from hofradar.config import SearchProfile
from hofradar.contracts import GeoResult, NormalizedListing
from hofradar.geo.distance import haversine_km
from hofradar.geo.geocoding import geocode
from hofradar.geo.routing import route_distance


def _build_geocode_queries(listing: NormalizedListing) -> list[str]:
    """Candidate geocode queries, most specific first, falling back to less
    specific as fields are missing."""
    street = (listing.street or "").strip()
    postcode = (listing.postcode or "").strip()
    town = (listing.town or "").strip()
    district = (listing.district or "").strip()

    queries: list[str] = []

    def add(raw: str) -> None:
        cleaned = " ".join(raw.split()).strip(", ").strip()
        if cleaned and cleaned not in queries:
            queries.append(cleaned)

    if street and (postcode or town):
        add(f"{street}, {postcode} {town}")
    elif street:
        add(street)
    if postcode and town:
        add(f"{postcode} {town}")
    if town:
        add(town)
    if postcode:
        add(postcode)
    if district:
        add(district)

    return queries


async def locate(session: Session, listing: NormalizedListing, profile: SearchProfile) -> GeoResult:
    """Geocode a listing, compute its air distance from ``profile.center``,
    and - only if that puts it inside the air radius - the real road
    distance. Never infers one distance from the other."""
    result = GeoResult(precision="none")
    for query in _build_geocode_queries(listing):
        result = await geocode(session, query, country="DE")
        if result.lat is not None and result.lon is not None:
            break

    center = (profile.center.lat, profile.center.lon)
    if result.lat is not None and result.lon is not None:
        result.distance_air_km = haversine_km(center, (result.lat, result.lon))

    if result.lat is not None and within_air_radius(result.distance_air_km, profile):
        km, minutes = await route_distance(session, center, (result.lat, result.lon))
        if km is not None and minutes is not None:
            result.distance_driving_km = km
            result.distance_driving_minutes = minutes
            result.routed = True

    return result


def within_air_radius(distance_air_km: float | None, profile: SearchProfile) -> bool:
    """Whether the straight-line distance is inside the air radius.

    An unknown distance (``None``) is always out of range - never "maybe in".
    """
    if distance_air_km is None:
        return False
    return distance_air_km <= profile.radius.air_km_max


def within_driving_radius(distance_driving_km: float | None, profile: SearchProfile) -> bool | None:
    """Whether the real road distance is inside the hard driving limit.

    Returns ``None`` when the distance is unknown (not yet routed, or
    routing failed) so callers must consciously decide what to do - see
    ``SearchProfile.radius.require_driving_check`` / ``GateConfig.reject_unrouted``.
    """
    if distance_driving_km is None:
        return None
    return distance_driving_km <= profile.radius.effective_driving_hard


def driving_band(distance_driving_km: float | None, profile: SearchProfile) -> str:
    """Coarse bucket for the road distance: within_soft | within_hard | beyond | unknown."""
    if distance_driving_km is None:
        return "unknown"
    if distance_driving_km <= profile.radius.effective_driving_soft:
        return "within_soft"
    if distance_driving_km <= profile.radius.effective_driving_hard:
        return "within_hard"
    return "beyond"
