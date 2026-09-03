"""route_distance(): caching, and the "never fall back to air distance" rule.

Any failure - timeout, bad status, malformed OSRM response - must produce
(None, None). There is no code path in this module that substitutes the
haversine distance for a missing road distance.
"""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from hofradar.geo import route_distance

OSRM_PATTERN = re.compile(r"^https://router\.project-osrm\.org/route/v1/driving/.*")

ORIGIN = (47.907, 11.840)
DEST = (48.14566132398814, 12.840591355642369)  # exactly 79.0 km air from ORIGIN

OSRM_OK = {
    "code": "Ok",
    "routes": [{"distance": 134000.0, "duration": 6300.0}],  # 134.0 km, 105.0 min
}


@pytest.mark.asyncio
async def test_route_distance_success(session):
    with respx.mock:
        respx.route(url__regex=OSRM_PATTERN).mock(return_value=httpx.Response(200, json=OSRM_OK))
        km, minutes = await route_distance(session, ORIGIN, DEST)

    assert km == pytest.approx(134.0)
    assert minutes == pytest.approx(105.0)


@pytest.mark.asyncio
async def test_route_distance_caches_after_first_call(session):
    with respx.mock:
        route = respx.route(url__regex=OSRM_PATTERN).mock(
            return_value=httpx.Response(200, json=OSRM_OK)
        )
        await route_distance(session, ORIGIN, DEST)
        await route_distance(session, ORIGIN, DEST)

    assert route.call_count == 1


@pytest.mark.asyncio
async def test_route_distance_failure_returns_none_none(session):
    with respx.mock:
        respx.route(url__regex=OSRM_PATTERN).mock(side_effect=httpx.ConnectTimeout("timed out"))
        km, minutes = await route_distance(session, ORIGIN, DEST)

    assert (km, minutes) == (None, None)


@pytest.mark.asyncio
async def test_route_distance_no_route_found_returns_none_none(session):
    with respx.mock:
        respx.route(url__regex=OSRM_PATTERN).mock(
            return_value=httpx.Response(200, json={"code": "NoRoute", "routes": []})
        )
        km, minutes = await route_distance(session, ORIGIN, DEST)

    assert (km, minutes) == (None, None)


@pytest.mark.asyncio
async def test_route_distance_http_error_status_returns_none_none(session):
    with respx.mock:
        respx.route(url__regex=OSRM_PATTERN).mock(return_value=httpx.Response(500))
        km, minutes = await route_distance(session, ORIGIN, DEST)

    assert (km, minutes) == (None, None)


@pytest.mark.asyncio
async def test_route_distance_offline_mode_returns_none_none_without_network(session, monkeypatch):
    monkeypatch.setenv("HOFRADAR_OFFLINE", "1")
    with respx.mock:
        respx.route(url__regex=OSRM_PATTERN).mock(
            side_effect=AssertionError("must not hit network")
        )
        km, minutes = await route_distance(session, ORIGIN, DEST)

    assert (km, minutes) == (None, None)
