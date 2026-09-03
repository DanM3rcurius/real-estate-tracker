"""The blocking key must be coarse enough to survive portal-to-portal noise."""

from __future__ import annotations

from hofradar.dedupe import fingerprint
from hofradar.dedupe.fingerprint import LAND_BUCKET_SQM, PRICE_BUCKET_EUR


def test_fingerprint_is_stable_and_hex(make_listing):
    listing = make_listing(land_sqm=8500, living_sqm=220, price=790_000, year_built=1890)
    first = fingerprint(listing)
    assert first == fingerprint(listing)
    assert len(first) == 32
    int(first, 16)  # raises if it is not hex


def test_small_numeric_noise_lands_in_the_same_bucket(make_listing):
    """The same farm on two portals: 8500 vs 8620 m2, 790k vs 789k."""
    a = make_listing(land_sqm=8500, living_sqm=220, price=790_000, year_built=1890)
    b = make_listing(land_sqm=8620, living_sqm=215, price=789_000, year_built=1893)
    assert fingerprint(a) == fingerprint(b)


def test_genuinely_different_objects_land_in_different_buckets(make_listing):
    a = make_listing(land_sqm=8500, living_sqm=220, price=790_000, year_built=1890)
    b = make_listing(
        land_sqm=8500 + 4 * LAND_BUCKET_SQM,
        living_sqm=220,
        price=790_000 + 5 * PRICE_BUCKET_EUR,
        year_built=1960,
    )
    assert fingerprint(a) != fingerprint(b)


def test_missing_fields_do_not_explode(make_listing):
    bare = make_listing(land_sqm=None, living_sqm=None, price=None, year_built=None)
    assert len(fingerprint(bare)) == 32


def test_geo_cell_substitutes_for_a_missing_postcode(make_listing):
    a = make_listing(postcode=None, land_sqm=8500)
    b = make_listing(postcode=None, land_sqm=8500)
    assert fingerprint(a, geo=(47.9070, 11.8400)) == fingerprint(b, geo=(47.9071, 11.8401))
    assert fingerprint(a, geo=(47.9070, 11.8400)) != fingerprint(b, geo=(48.5000, 11.8400))


def test_property_and_listing_fingerprint_the_same_way(make_listing, make_property):
    listing = make_listing(land_sqm=8500, living_sqm=220, price=790_000, year_built=1890)
    prop = make_property(
        town=listing.town,
        postcode=listing.postcode,
        land_sqm=8480,
        living_sqm=222,
        price=791_000,
        year_built=1891,
    )
    assert fingerprint(listing) == fingerprint(prop)
