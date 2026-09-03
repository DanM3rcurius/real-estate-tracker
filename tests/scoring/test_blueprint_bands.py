"""With the shipped defaults, the fractional bands must reproduce the
blueprint's absolute kilometre and euro thresholds exactly.

This is the test that lets the rest of the codebase talk in fractions without
anybody having to trust that the arithmetic works out.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from factories import make_property

from hofradar.config import SearchProfile, load_profile
from hofradar.scoring import fit_score

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"

#: (air distance in km, points) - the blueprint's 30 / 50 / 65 / 80 km bands.
GEOGRAPHY_CASES = [
    (0.0, 15.0),
    (29.9, 15.0),
    (30.0, 15.0),
    (30.1, 13.0),
    (50.0, 13.0),
    (50.1, 10.0),
    (65.0, 10.0),
    (65.1, 6.0),
    (80.0, 6.0),
    (80.1, 0.0),
]

#: (asking price, points) - the blueprint's 400k / 550k / 650k / 750k / 850k bands.
PRICE_CASES = [
    (250_000, 20.0),
    (400_000, 20.0),
    (400_001, 18.0),
    (550_000, 18.0),
    (550_001, 16.0),
    (650_000, 16.0),
    (650_001, 13.0),
    (750_000, 13.0),
    (750_001, 5.0),
    (850_000, 5.0),
    (850_001, 0.0),
]


@pytest.fixture(params=["code default", "config/search.yaml"])
def default_profile(request: pytest.FixtureRequest) -> SearchProfile:
    if request.param == "code default":
        return SearchProfile()
    return load_profile(CONFIG_DIR)


def test_the_shipped_defaults_are_the_blueprint_sliders(default_profile: SearchProfile) -> None:
    assert default_profile.radius.air_km_max == 80
    assert default_profile.budget.total_budget_max == 1_200_000
    assert default_profile.budget.effective_purchase_target_max == 750_000
    assert default_profile.budget.effective_purchase_negotiation_max == 850_000
    assert default_profile.budget.effective_purchase_hard_max == 900_000
    assert default_profile.budget.effective_total_exceptional_max == 1_350_000
    assert default_profile.budget.effective_total_hard_max == 1_500_000


@pytest.mark.parametrize(("distance_km", "expected"), GEOGRAPHY_CASES)
def test_geography_bands_reproduce_30_50_65_80_km(
    default_profile: SearchProfile, distance_km: float, expected: float
) -> None:
    prop = make_property(distance_air_km=distance_km)
    _, breakdown = fit_score(prop, default_profile)
    assert breakdown["geography_score"] == expected


@pytest.mark.parametrize(("price", "expected"), PRICE_CASES)
def test_price_bands_reproduce_400k_550k_650k_750k_850k(
    default_profile: SearchProfile, price: float, expected: float
) -> None:
    prop = make_property(price=price)
    _, breakdown = fit_score(prop, default_profile)
    assert breakdown["price_score"] == expected


def test_a_cheap_property_earns_no_bonus_beyond_the_top_band(
    default_profile: SearchProfile,
) -> None:
    """Cheapness is a deal_score question, not extra fit points."""
    cheap = fit_score(make_property(price=90_000), default_profile)[1]["price_score"]
    on_target = fit_score(make_property(price=400_000), default_profile)[1]["price_score"]
    assert cheap == on_target == 20.0


def test_halving_the_distance_slider_halves_the_geography_bands() -> None:
    """The same fractions, a different slider: 15 / 25 / 32.5 / 40 km."""
    profile = SearchProfile.model_validate({"radius": {"air_km_max": 40}})
    for distance_km, expected in [(15.0, 15.0), (25.0, 13.0), (32.5, 10.0), (40.0, 6.0),
                                  (40.1, 0.0)]:
        prop = make_property(distance_air_km=distance_km)
        assert fit_score(prop, profile)[1]["geography_score"] == expected
