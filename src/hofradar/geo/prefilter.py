"""A free, offline "is this worth a network round trip" question.

Bavaria-wide sources put most of their inventory outside an Upper-Bavarian
radius, and every detail page fetched for a Franconian object is crawl budget
spent on a public authority's server for nothing. The gazetteer already knows
where the towns are, so the question costs nothing.

The return type is deliberately three-valued. ``None`` means "the gazetteer has
never heard of this place", which is the single most likely description of a
hamlet with a farmstead in it - so it must reach the real geocoder, not the
bin. This function may only ever save a fetch; it may never reject a property.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hofradar.geo.distance import haversine_km
from hofradar.geo.gazetteer import lookup

if TYPE_CHECKING:  # pragma: no cover
    from hofradar.config import SearchProfile


def town_in_radius(town: str | None, profile: SearchProfile) -> bool | None:
    """Is this town inside the air radius? ``None`` when we cannot tell.

    Uses the air radius only, and only to decide whether to *fetch*. Air
    distance never stands in for road distance anywhere a property is judged.
    """
    if not town or not town.strip():
        return None
    entry = lookup(town)
    if entry is None:
        return None
    origin = (profile.center.lat, profile.center.lon)
    return haversine_km(origin, (entry.lat, entry.lon)) <= profile.radius.air_km_max
