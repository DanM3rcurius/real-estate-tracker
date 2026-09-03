"""haversine_km against known city-pair great-circle distances.

Expected values are the standard haversine great-circle distance (mean
Earth radius 6371.0088 km) between the cities' well-known coordinates -
independently reproducible, not tuned to this implementation.
"""

from __future__ import annotations

import pytest

from hofradar.geo import haversine_km

CASES = [
    pytest.param((52.5200, 13.4050), (48.1372, 11.5755), 504.3, id="berlin-munich"),
    pytest.param((51.5074, -0.1278), (48.8566, 2.3522), 343.6, id="london-paris"),
    pytest.param((40.7128, -74.0060), (34.0522, -118.2437), 3935.8, id="nyc-la"),
    pytest.param((-33.8688, 151.2093), (-37.8136, 144.9631), 713.4, id="sydney-melbourne"),
    pytest.param((48.1372, 11.5755), (48.2082, 16.3738), 355.9, id="munich-vienna"),
]


@pytest.mark.parametrize("a,b,expected_km", CASES)
def test_haversine_matches_known_distances(a, b, expected_km):
    assert haversine_km(a, b) == pytest.approx(expected_km, rel=0.01)


def test_haversine_is_symmetric():
    a, b = (47.907, 11.840), (48.1372, 11.5755)
    assert haversine_km(a, b) == pytest.approx(haversine_km(b, a), rel=1e-9)


def test_haversine_zero_for_identical_points():
    p = (47.907, 11.840)
    assert haversine_km(p, p) == pytest.approx(0.0, abs=1e-9)
