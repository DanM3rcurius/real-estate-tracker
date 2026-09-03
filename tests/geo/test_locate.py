"""locate(): end-to-end wiring of geocode -> air distance -> conditional route.

Blueprint Test 6, run through the real pipeline function: a listing that
geocodes to a point 79 km away as the crow flies but 134 km by road must
come back with routed=True, distance_air_km ~= 79, distance_driving_km ==
134 - never with the driving distance silently equal to (or derived from)
the air distance.
"""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from hofradar.config import SearchProfile
from hofradar.contracts import NormalizedListing
from hofradar.geo import driving_band, locate, within_air_radius, within_driving_radius
from hofradar.geo.geocoding import DEFAULT_NOMINATIM_URL

OSRM_PATTERN = re.compile(r"^https://router\.project-osrm\.org/route/v1/driving/.*")

CENTER = (47.907, 11.840)
# Exactly 79.0 km air-line from CENTER (verified via haversine offline).
IN_RANGE_POINT = (48.14566132398814, 12.840591355642369)
# Exactly 200.0 km air-line from CENTER - outside an 80 km air radius.
FAR_POINT = (48.78235915295898, 9.475749079569015)


def _profile() -> SearchProfile:
    return SearchProfile(
        center={"name": "test-center", "lat": CENTER[0], "lon": CENTER[1]},
        radius={"air_km_max": 80},
    )


def _listing(**overrides) -> NormalizedListing:
    fields = dict(
        source_key="test-source",
        url="https://example.test/listing/1",
        street="Dorfstrasse 3",
        postcode="83569",
        town="Vogtareuth",
        district="Landkreis Rosenheim",
    )
    fields.update(overrides)
    return NormalizedListing(**fields)


def _nominatim_hit(lat: float, lon: float) -> list[dict]:
    return [
        {
            "lat": str(lat),
            "lon": str(lon),
            "display_name": "Test Farmstead, Bayern, Deutschland",
            "addresstype": "house",
            "class": "building",
            "type": "house",
        }
    ]


@pytest.mark.asyncio
async def test_blueprint_test_6_full_pipeline(session):
    profile = _profile()
    listing = _listing()

    with respx.mock:
        respx.get(DEFAULT_NOMINATIM_URL).mock(
            return_value=httpx.Response(200, json=_nominatim_hit(*IN_RANGE_POINT))
        )
        respx.route(url__regex=OSRM_PATTERN).mock(
            return_value=httpx.Response(
                200, json={"code": "Ok", "routes": [{"distance": 134000.0, "duration": 6300.0}]}
            )
        )
        result = await locate(session, listing, profile)

    assert result.distance_air_km == pytest.approx(79.0, rel=0.001)
    assert result.routed is True
    assert result.distance_driving_km == pytest.approx(134.0)
    assert result.distance_driving_minutes == pytest.approx(105.0)

    # The point of the whole module: two different facts, two different verdicts.
    assert within_air_radius(result.distance_air_km, profile) is True
    assert within_driving_radius(result.distance_driving_km, profile) is False
    assert driving_band(result.distance_driving_km, profile) == "beyond"
    # And the air distance itself must never have leaked into the driving field.
    assert result.distance_driving_km != pytest.approx(result.distance_air_km, rel=0.1)


@pytest.mark.asyncio
async def test_locate_skips_routing_when_outside_air_radius(session):
    profile = _profile()
    listing = _listing()

    with respx.mock:
        respx.get(DEFAULT_NOMINATIM_URL).mock(
            return_value=httpx.Response(200, json=_nominatim_hit(*FAR_POINT))
        )
        osrm_route = respx.route(url__regex=OSRM_PATTERN).mock(
            side_effect=AssertionError("routing must not be attempted outside the air radius")
        )
        result = await locate(session, listing, profile)

    assert result.distance_air_km == pytest.approx(200.0, rel=0.001)
    assert within_air_radius(result.distance_air_km, profile) is False
    assert osrm_route.call_count == 0
    assert result.routed is False
    assert result.distance_driving_km is None
    assert result.distance_driving_minutes is None


@pytest.mark.asyncio
async def test_locate_route_failure_leaves_routed_false_not_backfilled(session):
    profile = _profile()
    listing = _listing()

    with respx.mock:
        respx.get(DEFAULT_NOMINATIM_URL).mock(
            return_value=httpx.Response(200, json=_nominatim_hit(*IN_RANGE_POINT))
        )
        respx.route(url__regex=OSRM_PATTERN).mock(side_effect=httpx.ConnectTimeout("timed out"))
        result = await locate(session, listing, profile)

    assert result.distance_air_km == pytest.approx(79.0, rel=0.001)
    assert result.routed is False
    assert result.distance_driving_km is None
    assert result.distance_driving_minutes is None
    # within_driving_radius must report "unknown", never "in range" or "out of range".
    assert within_driving_radius(result.distance_driving_km, profile) is None


@pytest.mark.asyncio
async def test_locate_unresolvable_listing_returns_empty_result(session):
    profile = _profile()
    listing = _listing(street=None, postcode=None, town=None, district=None)

    result = await locate(session, listing, profile)  # no HTTP mocked -> must not be called

    assert result.lat is None
    assert result.distance_air_km is None
    assert result.routed is False


@pytest.mark.asyncio
async def test_locate_prefers_most_specific_query_first(session):
    profile = _profile()
    listing = _listing()

    with respx.mock:
        route = respx.get(DEFAULT_NOMINATIM_URL).mock(
            return_value=httpx.Response(200, json=_nominatim_hit(*IN_RANGE_POINT))
        )
        respx.route(url__regex=OSRM_PATTERN).mock(
            return_value=httpx.Response(
                200, json={"code": "Ok", "routes": [{"distance": 10000.0, "duration": 600.0}]}
            )
        )
        await locate(session, listing, profile)

    first_query = route.calls[0].request.url.params["q"]
    assert "Dorfstrasse 3" in first_query
