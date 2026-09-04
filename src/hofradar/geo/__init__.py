"""Location intelligence: geocoding, air distance, and real road distance.

Air distance (haversine, instant, free) and driving distance (a real OSRM
road route, slow and rate-limited) are two different facts about a property,
and this package never lets one stand in for the other. A farm can be 79 km
away as the crow flies and 134 km by the only road that reaches it -
``within_air_radius`` and ``within_driving_radius`` answer two different
questions, and ``GeoResult.routed`` tells the rest of the pipeline whether
the driving fields are a real measurement or simply absent. ``routed=False``
is a fact ("we don't know yet"), never quietly treated as "close enough".

Everything that talks to Nominatim or OSRM is cached in ``GeoCache`` (a
cache read always happens before a network call, negative results included)
and rate-limited to at most one request per second per provider, per
Nominatim's usage policy. Set ``HOFRADAR_OFFLINE=1`` to resolve addresses
from the bundled Upper-Bavarian gazetteer instead of the network - road
routing then always reports ``(None, None)``, never a distance quietly
substituted from the air line.
"""

from __future__ import annotations

from hofradar.geo.distance import haversine_km
from hofradar.geo.geocoding import geocode
from hofradar.geo.locate import (
    driving_band,
    locate,
    within_air_radius,
    within_driving_radius,
)
from hofradar.geo.prefilter import town_in_radius
from hofradar.geo.routing import route_distance

__all__ = [
    "haversine_km",
    "geocode",
    "route_distance",
    "locate",
    "within_air_radius",
    "within_driving_radius",
    "driving_band",
    "town_in_radius",
]
