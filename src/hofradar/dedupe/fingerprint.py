"""The coarse blocking key.

A fingerprint is *not* proof of identity. It is a cheap, indexable bucket that
answers "which handful of rows is it even worth comparing this listing
against?". It is deliberately coarse - land to the nearest 500 m2, price to the
nearest 10k - because the same farm advertised on five portals will differ in
every one of those numbers by a few percent, and a strict key would put the
five copies in five different buckets, which is exactly the failure mode this
whole package exists to prevent.

Being coarse means collisions. That is fine and intended: ``compare`` decides,
``fingerprint`` only narrows.
"""

from __future__ import annotations

import hashlib
from typing import Any

from hofradar.dedupe._facts import GeoLike, facts_of
from hofradar.dedupe._util import round_to, slug

#: Bucket widths. Tunable: wider buckets mean more candidates and fewer misses.
LAND_BUCKET_SQM = 500.0
LIVING_BUCKET_SQM = 25.0
PRICE_BUCKET_EUR = 10_000.0
YEAR_BUCKET = 10

#: Degrees of latitude/longitude per geo cell when no postcode is known.
#: 0.01 deg is roughly 1.1 km north-south, ~0.75 km east-west in Bavaria.
GEO_CELL_DEGREES = 0.01

_DIGEST_SIZE = 16  # 32 hex characters, comfortably inside Property.fingerprint


def _bucket(value: float | None, step: float) -> str:
    rounded = round_to(value, step)
    return "" if rounded is None else f"{rounded:.0f}"


def _geo_cell(lat: float | None, lon: float | None) -> str:
    if lat is None or lon is None:
        return ""
    cell_lat = round(lat / GEO_CELL_DEGREES)
    cell_lon = round(lon / GEO_CELL_DEGREES)
    return f"{cell_lat}:{cell_lon}"


def fingerprint(obj: Any, *, geo: GeoLike = None) -> str:
    """Stable blake2s hex over a coarse bucket of identity.

    Accepts a ``NormalizedListing`` or a ``Property``. ``geo`` is optional and
    only used to derive a geo cell for a listing that has no postcode yet.
    """
    facts = facts_of(obj, geo=geo)
    location = facts.postcode or _geo_cell(facts.lat, facts.lon)
    parts = [
        location.strip(),
        slug(facts.town),
        _bucket(facts.land_sqm, LAND_BUCKET_SQM),
        _bucket(facts.living_sqm, LIVING_BUCKET_SQM),
        _bucket(facts.price, PRICE_BUCKET_EUR),
        "" if facts.year_built is None else str((facts.year_built // YEAR_BUCKET) * YEAR_BUCKET),
    ]
    blob = "|".join(parts).encode("utf-8")
    return hashlib.blake2s(blob, digest_size=_DIGEST_SIZE).hexdigest()
