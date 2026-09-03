"""geocode(): caching, precision mapping, and the offline gazetteer path."""

from __future__ import annotations

import httpx
import pytest
import respx

from hofradar.geo import geocode
from hofradar.geo.geocoding import DEFAULT_NOMINATIM_URL, USER_AGENT

NOMINATIM_HIT = [
    {
        "lat": "47.9508",
        "lon": "12.1917",
        "display_name": "Vogtareuth, Landkreis Rosenheim, Bayern, Deutschland",
        "addresstype": "village",
        "class": "place",
        "type": "village",
    }
]


@pytest.mark.asyncio
async def test_geocode_caches_after_first_call(session):
    with respx.mock:
        route = respx.get(DEFAULT_NOMINATIM_URL).mock(
            return_value=httpx.Response(200, json=NOMINATIM_HIT)
        )

        first = await geocode(session, "Vogtareuth", country="DE")
        second = await geocode(session, "Vogtareuth", country="DE")

    assert route.call_count == 1, "second geocode() call must be served from cache"
    assert first.lat == pytest.approx(47.9508)
    assert first.lon == pytest.approx(12.1917)
    assert first.precision == "town"
    assert first.provider == "nominatim"
    assert second.lat == first.lat
    assert second.lon == first.lon
    assert second.precision == first.precision


@pytest.mark.asyncio
async def test_geocode_sends_descriptive_user_agent(session):
    with respx.mock:
        route = respx.get(DEFAULT_NOMINATIM_URL).mock(
            return_value=httpx.Response(200, json=NOMINATIM_HIT)
        )
        await geocode(session, "Vogtareuth", country="DE")

    sent_headers = route.calls.last.request.headers
    assert sent_headers["User-Agent"] == USER_AGENT


@pytest.mark.asyncio
async def test_geocode_caches_negative_result(session):
    with respx.mock:
        route = respx.get(DEFAULT_NOMINATIM_URL).mock(return_value=httpx.Response(200, json=[]))

        first = await geocode(session, "Nirgendwo, das nicht existiert", country="DE")
        second = await geocode(session, "Nirgendwo, das nicht existiert", country="DE")

    assert route.call_count == 1, "a negative result must be cached too"
    assert first.lat is None and first.lon is None
    assert first.precision == "none"
    assert second.lat is None and second.lon is None


@pytest.mark.asyncio
async def test_geocode_precision_exact_for_house(session):
    payload = [
        {
            "lat": "47.86",
            "lon": "12.01",
            "display_name": "Musterstrasse 1, Bad Aibling",
            "addresstype": "house",
            "class": "building",
            "type": "house",
        }
    ]
    with respx.mock:
        respx.get(DEFAULT_NOMINATIM_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await geocode(session, "Musterstrasse 1, Bad Aibling", country="DE")

    assert result.precision == "exact"


@pytest.mark.asyncio
async def test_geocode_precision_street_for_road(session):
    payload = [
        {
            "lat": "47.86",
            "lon": "12.01",
            "display_name": "Musterstrasse, Bad Aibling",
            "addresstype": "road",
            "class": "highway",
            "type": "residential",
        }
    ]
    with respx.mock:
        respx.get(DEFAULT_NOMINATIM_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await geocode(session, "Musterstrasse, Bad Aibling", country="DE")

    assert result.precision == "street"


@pytest.mark.asyncio
async def test_geocode_precision_postcode(session):
    payload = [
        {
            "lat": "47.86",
            "lon": "12.01",
            "display_name": "83043, Bayern, Deutschland",
            "addresstype": "postcode",
            "class": "place",
            "type": "postcode",
        }
    ]
    with respx.mock:
        respx.get(DEFAULT_NOMINATIM_URL).mock(return_value=httpx.Response(200, json=payload))
        result = await geocode(session, "83043", country="DE")

    assert result.precision == "postcode"


@pytest.mark.asyncio
async def test_geocode_network_failure_returns_none_result_not_cached_forever_wrong(session):
    with respx.mock:
        respx.get(DEFAULT_NOMINATIM_URL).mock(side_effect=httpx.ConnectError("boom"))
        result = await geocode(session, "Some Place", country="DE")

    assert result.lat is None and result.lon is None
    assert result.precision == "none"


@pytest.mark.asyncio
async def test_geocode_offline_gazetteer_hit(session, monkeypatch):
    monkeypatch.setenv("HOFRADAR_OFFLINE", "1")
    with respx.mock:  # if this fires, the offline path made a real HTTP call
        respx.get(DEFAULT_NOMINATIM_URL).mock(side_effect=AssertionError("must not hit network"))
        result = await geocode(session, "Bad Aibling", country="DE")

    assert result.provider == "gazetteer"
    assert result.lat == pytest.approx(47.8656, abs=0.01)
    assert result.lon == pytest.approx(12.0116, abs=0.01)
    assert result.precision == "town"


@pytest.mark.asyncio
async def test_geocode_offline_gazetteer_matches_full_address(session, monkeypatch):
    monkeypatch.setenv("HOFRADAR_OFFLINE", "1")
    result = await geocode(session, "Hauptstrasse 5, 83043 Bad Aibling", country="DE")
    assert result.provider == "gazetteer"
    assert result.lat is not None


@pytest.mark.asyncio
async def test_geocode_offline_gazetteer_miss(session, monkeypatch):
    monkeypatch.setenv("HOFRADAR_OFFLINE", "1")
    result = await geocode(session, "Timbuktu, Mali", country="DE")
    assert result.lat is None
    assert result.precision == "none"
