"""Air vs. driving radius logic - the whole point of this module.

Blueprint Test 6: a property 79 km away as the crow flies but 134 km by the
only road that reaches it must be inside the air radius and OUTSIDE the
driving radius. Air distance and driving distance are two different facts
and no code path here may let one stand in for the other.
"""

from __future__ import annotations

from hofradar.config import SearchProfile
from hofradar.geo import driving_band, within_air_radius, within_driving_radius


def _profile(**radius_overrides) -> SearchProfile:
    return SearchProfile(radius={"air_km_max": 80, **radius_overrides})


def test_derived_driving_limits_from_air_km_max():
    profile = _profile()
    assert profile.radius.effective_driving_soft == 100.0
    assert profile.radius.effective_driving_hard == 116.0


def test_blueprint_test_6_air_in_range_driving_out_of_range():
    profile = _profile()  # air_km_max=80 -> soft=100.0, hard=116.0

    air_km = 79.0
    driving_km = 134.0

    assert within_air_radius(air_km, profile) is True
    assert within_driving_radius(driving_km, profile) is False
    assert driving_band(driving_km, profile) == "beyond"

    # The air distance must never have been able to satisfy the driving check.
    assert air_km <= profile.radius.air_km_max
    assert driving_km > profile.radius.effective_driving_hard


def test_within_air_radius_none_is_false():
    profile = _profile()
    assert within_air_radius(None, profile) is False


def test_within_driving_radius_none_is_none():
    profile = _profile()
    assert within_driving_radius(None, profile) is None


def test_within_driving_radius_at_and_beyond_hard_limit():
    profile = _profile()
    assert within_driving_radius(116.0, profile) is True  # inclusive at the hard limit
    assert within_driving_radius(116.01, profile) is False


def test_driving_band_all_buckets():
    profile = _profile()
    assert driving_band(None, profile) == "unknown"
    assert driving_band(50.0, profile) == "within_soft"
    assert driving_band(110.0, profile) == "within_hard"
    assert driving_band(200.0, profile) == "beyond"


def test_driving_limits_respect_explicit_overrides():
    profile = _profile(driving_km_soft_max=90, driving_km_hard_max=120)
    assert profile.radius.effective_driving_soft == 90
    assert profile.radius.effective_driving_hard == 120
