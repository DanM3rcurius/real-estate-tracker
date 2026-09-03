"""A single flat view over the two things we ever have to compare.

``compare`` must work on any mix of a freshly normalised listing (no database
row yet) and a stored ``Property`` (canonical facts, images, source rows). The
two have different attribute names and different shapes - a listing carries a
flat list of image hashes, a property carries ``Image`` rows - so both are
projected onto one small dataclass here. Every dimension of the similarity
model then reads from ``ListingFacts`` and nothing else, which keeps the
scoring code free of ``isinstance`` branches.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from hofradar.contracts import GeoResult, NormalizedListing
from hofradar.db.models import Property
from hofradar.dedupe._util import canonical_url

GeoLike = GeoResult | tuple[float | None, float | None] | None

#: Geocode precisions that pin a building rather than a village.
PRECISE_GEO = frozenset({"exact", "street"})


@dataclass(slots=True)
class ListingFacts:
    """Comparable identity of one candidate, listing or property alike."""

    property_id: int | None = None
    title: str | None = None
    description: str | None = None
    street: str | None = None
    postcode: str | None = None
    town: str | None = None
    lat: float | None = None
    lon: float | None = None
    #: 'exact' | 'street' | 'town' | 'postcode' | 'none'. A town centroid is
    #: shared by every farm in the village, so it can never prove sameness.
    geo_precision: str = "none"
    land_sqm: float | None = None
    living_sqm: float | None = None
    price: float | None = None
    year_built: int | None = None
    property_type: str | None = None
    image_hashes: list[str] = field(default_factory=list)
    #: ``(source_key, external_id)`` pairs. Equality of a pair is proof.
    source_ids: set[tuple[str, str]] = field(default_factory=set)
    #: Every listing URL this candidate is known under, canonicalised (see
    #: :func:`hofradar.dedupe._util.canonical_url`). Unlike ``source_ids``
    #: these are comparable ACROSS sources: an external id is one source's
    #: private numbering, but a URL names one page on one host, so two sources
    #: publishing the same one are publishing the same listing.
    canonical_urls: set[str] = field(default_factory=set)

    @property
    def coords(self) -> tuple[float, float] | None:
        if self.lat is None or self.lon is None:
            return None
        return (self.lat, self.lon)


def _geo_triple(geo: GeoLike) -> tuple[float | None, float | None, str]:
    if geo is None:
        return (None, None, "none")
    if isinstance(geo, GeoResult):
        return (geo.lat, geo.lon, geo.precision or "none")
    return (geo[0], geo[1], "exact" if geo[0] is not None else "none")


def facts_of(obj: Any, *, geo: GeoLike = None) -> ListingFacts:
    """Project a ``NormalizedListing``, a ``Property`` or ``ListingFacts``.

    ``geo`` supplies coordinates for a listing that has not been geocoded into
    a row yet; it never overrides coordinates the object already knows.
    """
    if isinstance(obj, ListingFacts):
        lat, lon, precision = _geo_triple(geo)
        if obj.lat is None and lat is not None:
            obj.lat, obj.lon, obj.geo_precision = lat, lon, precision
        return obj
    if isinstance(obj, Property):
        return _facts_from_property(obj, geo=geo)
    if isinstance(obj, NormalizedListing):
        return _facts_from_listing(obj, geo=geo)
    raise TypeError(f"cannot compare object of type {type(obj)!r}")


def _facts_from_listing(listing: NormalizedListing, *, geo: GeoLike = None) -> ListingFacts:
    lat, lon, precision = _geo_triple(geo)
    source_ids: set[tuple[str, str]] = set()
    if listing.external_id and listing.source_key:
        source_ids.add((listing.source_key, str(listing.external_id)))
    return ListingFacts(
        property_id=None,
        title=listing.title,
        description=listing.description,
        street=listing.street,
        postcode=listing.postcode,
        town=listing.town,
        lat=lat,
        lon=lon,
        geo_precision=precision,
        land_sqm=listing.land_sqm,
        living_sqm=listing.living_sqm,
        price=listing.price,
        year_built=listing.year_built,
        property_type=listing.property_type,
        image_hashes=[h for h in (listing.image_hashes or []) if h],
        source_ids=source_ids,
        canonical_urls={url for url in (canonical_url(listing.url),) if url},
    )


def _facts_from_property(prop: Property, *, geo: GeoLike = None) -> ListingFacts:
    lat, lon, precision = prop.lat, prop.lon, prop.geo_precision or "none"
    if lat is None or lon is None:
        lat, lon, precision = _geo_triple(geo)
    source_ids: set[tuple[str, str]] = set()
    canonical_urls: set[str] = set()
    for ps in prop.property_sources:
        if ps.external_id and ps.source is not None:
            source_ids.add((ps.source.key, str(ps.external_id)))
        canonical = canonical_url(ps.url)
        if canonical:
            canonical_urls.add(canonical)
    return ListingFacts(
        property_id=prop.id,
        title=prop.canonical_title,
        description=prop.description,
        street=prop.street,
        postcode=prop.postcode,
        town=prop.town,
        lat=lat,
        lon=lon,
        geo_precision=precision,
        land_sqm=prop.land_sqm,
        living_sqm=prop.living_sqm,
        price=prop.price,
        year_built=prop.year_built,
        property_type=prop.property_type,
        image_hashes=[img.phash for img in prop.images if img.phash],
        source_ids=source_ids,
        canonical_urls=canonical_urls,
    )
