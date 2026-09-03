"""Forward geocoding via Nominatim, with caching, rate limiting, and an
offline gazetteer fallback.

Precision matters more here than almost anywhere else in the pipeline: it
feeds directly into the confidence score's location term, so systematically
getting "street" versus "town" wrong flatters or punishes every listing.
See :func:`_precision` for the exact mapping from Nominatim's response
fields.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from sqlalchemy.orm import Session

from hofradar.contracts import GeoResult
from hofradar.geo import gazetteer
from hofradar.geo.cache import cache_get, cache_put
from hofradar.geo.ratelimit import nominatim_limiter

#: Overridable so a user can point at their own Nominatim instance.
DEFAULT_NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

#: Nominatim's usage policy requires a descriptive User-Agent identifying the
#: application (not a generic library UA).
USER_AGENT = "hofradar/0.1 (+https://github.com/DanM3rcurius/real-estate-tracker)"

# addresstype/type values (Nominatim jsonv2) mapped to our four precision
# buckets. class == "building" and class == "highway" are checked directly
# since they are more reliable than the free-form type field.
_EXACT_TYPES = {"house"}
_STREET_TYPES = {"road", "pedestrian", "footway", "living_street", "residential_road"}
_TOWN_TYPES = {
    "village",
    "town",
    "city",
    "municipality",
    "hamlet",
    "suburb",
    "city_district",
    "township",
    "borough",
    "isolated_dwelling",
}
_POSTCODE_TYPES = {"postcode"}


def _nominatim_url() -> str:
    return os.environ.get("HOFRADAR_NOMINATIM_URL", DEFAULT_NOMINATIM_URL)


def _cache_key(query: str, country: str) -> str:
    normalized = " ".join(query.strip().lower().split())
    return f"{country.lower()}:{normalized}"


def _precision(item: dict[str, Any]) -> str:
    """Map a Nominatim result item to exact | street | town | postcode | none."""
    addresstype = (item.get("addresstype") or "").lower()
    klass = (item.get("class") or "").lower()
    type_ = (item.get("type") or "").lower()

    if klass == "building" or addresstype in _EXACT_TYPES or type_ in _EXACT_TYPES:
        return "exact"
    if klass == "highway" or addresstype in _STREET_TYPES or type_ in _STREET_TYPES:
        return "street"
    if addresstype in _TOWN_TYPES or type_ in _TOWN_TYPES:
        return "town"
    if addresstype in _POSTCODE_TYPES or type_ in _POSTCODE_TYPES:
        return "postcode"
    return "none"


def _payload_from_result(result: GeoResult) -> dict[str, Any]:
    return {
        "found": result.lat is not None and result.lon is not None,
        "lat": result.lat,
        "lon": result.lon,
        "precision": result.precision,
        "display_name": result.display_name,
        "provider": result.provider,
    }


def _result_from_payload(payload: dict[str, Any]) -> GeoResult:
    return GeoResult(
        lat=payload.get("lat"),
        lon=payload.get("lon"),
        precision=payload.get("precision", "none"),
        display_name=payload.get("display_name"),
        provider=payload.get("provider"),
    )


def _geocode_offline(query: str) -> GeoResult:
    entry = gazetteer.lookup(query)
    if entry is None:
        return GeoResult(precision="none", provider="gazetteer")
    return GeoResult(
        lat=entry.lat,
        lon=entry.lon,
        precision="town",
        display_name=f"{entry.name}, {entry.postcode}, Bayern, Deutschland",
        provider="gazetteer",
    )


async def _geocode_online(query: str, country: str) -> GeoResult:
    params = {
        "q": query,
        "format": "jsonv2",
        "addressdetails": "1",
        "countrycodes": country.lower(),
        "limit": "1",
    }
    headers = {"User-Agent": USER_AGENT}
    try:
        await nominatim_limiter.wait()
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(_nominatim_url(), params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return GeoResult(precision="none", provider="nominatim")

    if not data:
        return GeoResult(precision="none", provider="nominatim")

    item = data[0]
    try:
        lat = float(item["lat"])
        lon = float(item["lon"])
    except (KeyError, TypeError, ValueError):
        return GeoResult(precision="none", provider="nominatim")

    return GeoResult(
        lat=lat,
        lon=lon,
        precision=_precision(item),
        display_name=item.get("display_name"),
        provider="nominatim",
    )


async def geocode(session: Session, query: str, *, country: str = "DE") -> GeoResult:
    """Resolve a free-text address to coordinates.

    Cache-first (cached negative results included, so an unresolvable
    address is not re-asked every run). Set ``HOFRADAR_OFFLINE=1`` to resolve
    from the bundled Upper-Bavarian gazetteer instead of calling Nominatim -
    used by tests and air-gapped runs.
    """
    query = (query or "").strip()
    if not query:
        return GeoResult(precision="none")

    key = _cache_key(query, country)
    cached = cache_get(session, "geocode", key)
    if cached is not None:
        return _result_from_payload(cached)

    if os.environ.get("HOFRADAR_OFFLINE") == "1":
        result = _geocode_offline(query)
    else:
        result = await _geocode_online(query, country)

    cache_put(session, "geocode", key, _payload_from_result(result))
    return result
