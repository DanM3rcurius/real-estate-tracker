"""Blueprint Test 3 - the three Vogtareuth titles.

"Hofstelle in Vogtareuth", "Bauernhaus Vogtareuth" and "Sacherl bei
Vogtareuth" are the canonical trap. They may be one farm described by three
brokers, or three separate farms in one village, and the *words alone can never
tell you which*. The documented behaviour is therefore:

* merge when the numbers corroborate;
* flag ``needs_review`` and refuse to merge when they do not.
"""

from __future__ import annotations

import pytest

from hofradar.dedupe import compare
from hofradar.dedupe.compare import (
    DUPLICATE_THRESHOLD,
    MIN_CORROBORATING_DIMENSIONS,
    NEEDS_REVIEW_THRESHOLD,
)

TITLES = [
    "Hofstelle in Vogtareuth",
    "Bauernhaus Vogtareuth",
    "Sacherl bei Vogtareuth",
]


def _reasons(verdict) -> str:
    return " | ".join(verdict.reasons)


@pytest.mark.parametrize("title_a,title_b", [(TITLES[0], TITLES[1]), (TITLES[1], TITLES[2])])
def test_merges_when_numeric_fields_corroborate(make_listing, title_a, title_b):
    a = make_listing(
        title=title_a, land_sqm=8500, living_sqm=220, price=790_000, year_built=1890
    )
    b = make_listing(
        title=title_b, land_sqm=8620, living_sqm=214, price=789_000, year_built=1890
    )

    verdict = compare(a, b)

    assert verdict.is_duplicate is True
    assert verdict.confidence >= DUPLICATE_THRESHOLD
    assert "land_match" in _reasons(verdict)
    assert "living_match" in _reasons(verdict)


def test_location_and_type_alone_are_never_enough(make_listing):
    """No numeric field on either side: the answer is 'a human must look'."""
    a = make_listing(title=TITLES[0])
    b = make_listing(title=TITLES[2])

    verdict = compare(a, b)

    assert verdict.is_duplicate is False
    assert "needs_review" in _reasons(verdict)
    assert NEEDS_REVIEW_THRESHOLD <= verdict.confidence < DUPLICATE_THRESHOLD


def test_three_different_farms_in_one_village_stay_apart(make_listing):
    """Same town, same type, similar titles - but the numbers disagree."""
    hofstelle = make_listing(
        title=TITLES[0], land_sqm=8500, living_sqm=220, price=790_000, year_built=1890
    )
    bauernhaus = make_listing(
        title=TITLES[1], land_sqm=1200, living_sqm=160, price=520_000, year_built=1975
    )
    sacherl = make_listing(
        title=TITLES[2], land_sqm=3400, living_sqm=95, price=340_000, year_built=1930
    )

    for a, b in ((hofstelle, bauernhaus), (hofstelle, sacherl), (bauernhaus, sacherl)):
        verdict = compare(a, b)
        assert verdict.is_duplicate is False, _reasons(verdict)
        assert verdict.confidence < DUPLICATE_THRESHOLD


def test_identical_titles_alone_do_not_merge(make_listing):
    """Even a perfect title match is not proof - it is one dimension."""
    a = make_listing(title="Hofstelle in Vogtareuth", description="Schoenes Anwesen.")
    b = make_listing(title="Hofstelle in Vogtareuth", description="Schoenes Anwesen.")

    verdict = compare(a, b)

    assert verdict.is_duplicate is False
    assert "corroborating_dimensions: 0" in _reasons(verdict)


def test_geo_proximity_needs_precise_geocodes(make_listing, make_geo):
    """A shared town centroid must not masquerade as '0 m apart'."""
    a = make_listing(title=TITLES[0], land_sqm=8500, living_sqm=220)
    b = make_listing(title=TITLES[2], land_sqm=8500, living_sqm=220)

    town_centroid = make_geo(47.9070, 11.8400, precision="town")
    coarse = compare(a, b, a_geo=town_centroid, b_geo=town_centroid)
    assert "geo_coarse" in _reasons(coarse)
    assert "geo_same_object" not in _reasons(coarse)

    exact = compare(
        a,
        b,
        a_geo=make_geo(47.90700, 11.84000),
        b_geo=make_geo(47.90710, 11.84010),
    )
    assert "geo_same_object" in _reasons(exact)
    assert exact.is_duplicate is True


def test_far_apart_is_a_veto_even_with_matching_numbers(make_listing, make_geo):
    a = make_listing(land_sqm=8500, living_sqm=220, price=790_000, year_built=1890)
    b = make_listing(land_sqm=8500, living_sqm=220, price=790_000, year_built=1890)

    verdict = compare(a, b, a_geo=make_geo(47.9070, 11.8400), b_geo=make_geo(48.2000, 11.8400))

    assert verdict.is_duplicate is False
    assert "geo_far" in _reasons(verdict)


def test_shared_image_hash_is_near_proof(make_listing):
    a = make_listing(title=TITLES[0], image_hashes=["f0e1d2c3b4a59687"])
    b = make_listing(title=TITLES[2], image_hashes=["f0e1d2c3b4a59685"])  # 1 bit apart

    verdict = compare(a, b)

    assert verdict.is_duplicate is True
    assert verdict.confidence > 0.9
    assert "shared_image" in _reasons(verdict)


def test_same_external_id_on_the_same_source_is_proof(make_listing):
    a = make_listing(source_key="portal", external_id="ABC-123", title=TITLES[0])
    b = make_listing(source_key="portal", external_id="ABC-123", title=TITLES[2])

    verdict = compare(a, b)

    assert verdict.is_duplicate is True
    assert verdict.confidence == 1.0
    assert "same_external_id" in _reasons(verdict)


def test_missing_price_on_one_side_is_neutral(make_listing):
    """'Preis auf Anfrage' must not read as a price disagreement."""
    a = make_listing(land_sqm=8500, living_sqm=220, price=790_000, year_built=1890)
    b = make_listing(land_sqm=8500, living_sqm=220, price=None, year_built=1890)

    verdict = compare(a, b)

    assert "price_unknown" in _reasons(verdict)
    assert "price_mismatch" not in _reasons(verdict)
    assert verdict.is_duplicate is True


def test_duplicate_requires_multiple_independent_dimensions(make_listing):
    a = make_listing(title=TITLES[0], land_sqm=8500)
    b = make_listing(title=TITLES[0], land_sqm=8500)

    verdict = compare(a, b)

    assert verdict.is_duplicate is False
    assert f"corroborating_dimensions: {MIN_CORROBORATING_DIMENSIONS - 1}" in _reasons(verdict)
