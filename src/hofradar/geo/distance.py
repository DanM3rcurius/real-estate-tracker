"""Great-circle ("as the crow flies") distance between two points.

Air distance is a fast, exact-enough proxy for "is this even worth looking
at further" - it needs no network call and can be computed for every
candidate instantly. It is emphatically NOT a substitute for the real road
distance a Hofstelle costs to reach on a Sunday drive; see ``routing.py`` and
the package docstring in ``__init__.py`` for why the two are kept strictly
apart everywhere in this codebase.
"""

from __future__ import annotations

import math

#: Mean Earth radius in km (IUGG mean radius, WGS84-adjacent). Good enough
#: for regional real-estate distances; not for geodesy.
EARTH_RADIUS_KM = 6371.0088


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance between two ``(lat, lon)`` points, in kilometres.

    Pure function, no I/O. Accurate to well within 1% at the scale this
    project cares about (tens to low hundreds of km).
    """
    lat1, lon1 = a
    lat2, lon2 = b
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    #: Clamp for float noise right at h == 1.0 (antipodal points); asin needs [-1, 1].
    root = min(1.0, math.sqrt(h))
    return 2 * EARTH_RADIUS_KM * math.asin(root)
