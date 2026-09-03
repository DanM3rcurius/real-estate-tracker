"""Road-distance lookups via OSRM, with caching and rate limiting.

Air distance and driving distance are two different facts (see the package
docstring in ``__init__.py``). This module NEVER falls back to the haversine
distance: any failure - a timeout, a malformed response, OSRM reporting
"NoRoute" - is reported as ``(None, None)``, and it is the caller's job to
decide what an unmeasured route means for that listing (``locate`` leaves
``routed=False``; the pipeline's gate config decides whether that holds a
property back).
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from sqlalchemy.orm import Session

from hofradar.geo.cache import cache_get, cache_put
from hofradar.geo.ratelimit import osrm_limiter

#: Overridable so a user can point at their own OSRM instance.
DEFAULT_OSRM_URL = "https://router.project-osrm.org"


def _osrm_url() -> str:
    return os.environ.get("HOFRADAR_OSRM_URL", DEFAULT_OSRM_URL).rstrip("/")


def _cache_key(origin: tuple[float, float], dest: tuple[float, float]) -> str:
    """``"lat,lon->lat,lon"`` rounded to 4dp (~11m) - plenty for cache hits
    without treating float-noise as a distinct route."""
    o_lat, o_lon = origin
    d_lat, d_lon = dest
    return f"{o_lat:.4f},{o_lon:.4f}->{d_lat:.4f},{d_lon:.4f}"


async def _route_online(
    origin: tuple[float, float], dest: tuple[float, float]
) -> tuple[float | None, float | None]:
    o_lat, o_lon = origin
    d_lat, d_lon = dest
    url = f"{_osrm_url()}/route/v1/driving/{o_lon:.6f},{o_lat:.6f};{d_lon:.6f},{d_lat:.6f}"
    params = {"overview": "false"}
    try:
        await osrm_limiter.wait()
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
        response.raise_for_status()
        data: dict[str, Any] = response.json()
    except (httpx.HTTPError, ValueError):
        return None, None

    if data.get("code") != "Ok":
        return None, None
    routes = data.get("routes") or []
    if not routes:
        return None, None

    try:
        distance_m = float(routes[0]["distance"])
        duration_s = float(routes[0]["duration"])
    except (KeyError, TypeError, ValueError):
        return None, None

    return distance_m / 1000.0, duration_s / 60.0


async def route_distance(
    session: Session, origin: tuple[float, float], dest: tuple[float, float]
) -> tuple[float | None, float | None]:
    """Real road distance and driving time from ``origin`` to ``dest``.

    Returns ``(km, minutes)``. On any failure - including offline mode -
    returns ``(None, None)``; never substitutes the air distance.
    """
    key = _cache_key(origin, dest)
    cached = cache_get(session, "route", key)
    if cached is not None:
        return cached.get("km"), cached.get("minutes")

    if os.environ.get("HOFRADAR_OFFLINE") == "1":
        return None, None

    km, minutes = await _route_online(origin, dest)
    cache_put(session, "route", key, {"found": km is not None, "km": km, "minutes": minutes})
    return km, minutes
