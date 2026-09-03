"""A cheap, offline "is this town even worth fetching" check.

Three-valued on purpose. A town the gazetteer does not know must not be
silently discarded - the unknown case is exactly where an obscure hamlet with a
farmstead lives, so it falls through to the full geocoding path.
"""

from __future__ import annotations

from hofradar.geo import town_in_radius
from tests.geo.conftest import make_profile  # existing helper


def test_town_inside_the_radius_is_true() -> None:
    profile = make_profile(air_km_max=60)
    assert town_in_radius("Bad Aibling", profile) is True


def test_town_far_outside_the_radius_is_false() -> None:
    profile = make_profile(air_km_max=60)
    # "Nordhalben" (Upper Franconia) is not in the bundled Upper-Bavaria-only
    # gazetteer at all, which would exercise the None branch instead of
    # False. "Neumarkt-Sankt Veit" is a real gazetteer entry (Landkreis
    # Muehldorf) ~71 km from the Westham origin, so it is known and outside
    # a 60 km radius - the case this test is actually meant to cover.
    assert town_in_radius("Neumarkt-Sankt Veit", profile) is False


def test_unknown_town_is_none_so_the_caller_fetches_anyway() -> None:
    profile = make_profile(air_km_max=60)
    assert town_in_radius("Hinterdupfing", profile) is None


def test_missing_town_is_none() -> None:
    profile = make_profile(air_km_max=60)
    assert town_in_radius(None, profile) is None
    assert town_in_radius("", profile) is None
