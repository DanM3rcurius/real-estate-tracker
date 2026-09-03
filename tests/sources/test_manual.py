"""ManualAdapter: paste-ingest from plain text and from fetched/pasted HTML."""

from __future__ import annotations

import httpx
import pytest
import respx

from hofradar.sources.adapters.manual import ManualAdapter

PLAIN_EXPOSE = """\
Gepflegte Hofstelle mit Scheune in Alleinlage
Kaufpreis: 480.000 €
Wohnfläche: 195 m²
Grundstück: 4.200 m²
Baujahr: 1888
Ort: Feldkirchen-Westerham

Diese ehemalige Landwirtschaft bietet Stadel, Stall und viel
Entwicklungspotenzial. Kein Makler, provisionsfrei.

https://img.example.host/foto1.jpg
https://img.example.host/foto2.png
"""


@pytest.fixture
def adapter(make_source_config):
    cfg = make_source_config(key="manual", adapter="manual", role="primary")
    return ManualAdapter(cfg)


def test_ingest_text_from_plain_paste(adapter):
    listing = adapter.ingest_text("https://user-pasted.example/no-real-url", PLAIN_EXPOSE)

    assert listing.source_key == "manual"
    assert listing.title == "Gepflegte Hofstelle mit Scheune in Alleinlage"
    assert listing.price_raw == "480.000 €"
    assert listing.living_raw == "195 m²"
    assert listing.land_raw == "4.200 m²"
    assert listing.year_raw == "1888"
    assert listing.location_raw == "Feldkirchen-Westerham"
    assert "Entwicklungspotenzial" in listing.description
    assert listing.image_urls == [
        "https://img.example.host/foto1.jpg",
        "https://img.example.host/foto2.png",
    ]


def test_ingest_text_from_html(adapter, read_fixture):
    html = read_fixture("detail_live.html")
    listing = adapter.ingest_text("https://makler.example/hof-1", html)

    assert listing.title == "Hofstelle mit Scheune bei Feldkirchen-Westerham"
    assert listing.price_raw == "590.000 €"
    assert listing.living_raw == "210 m²"
    assert listing.land_raw == "6.500 m²"
    assert "Nebengebäuden" in listing.description or "Entwicklungspotenzial" in listing.description
    assert listing.image_urls == [
        "https://makler.example/bilder/titel.jpg",
        "https://makler.example/bilder/hof-1.jpg",
        "https://makler.example/bilder/hof-2.jpg",
    ]


@pytest.mark.asyncio
async def test_ingest_url_fetches_and_parses(adapter, read_fixture):
    html = read_fixture("detail_live.html")
    with respx.mock:
        respx.get("https://makler.example/hof-2").mock(return_value=httpx.Response(200, text=html))
        listing = await adapter.ingest_url("https://makler.example/hof-2")

    assert listing is not None
    assert listing.http_status == 200
    assert listing.listing_visible is True
    assert listing.title == "Hofstelle mit Scheune bei Feldkirchen-Westerham"


@pytest.mark.asyncio
async def test_ingest_url_detects_gone_listing(adapter, read_fixture):
    html = read_fixture("detail_gone.html")
    with respx.mock:
        respx.get("https://makler.example/hof-3").mock(return_value=httpx.Response(200, text=html))
        listing = await adapter.ingest_url("https://makler.example/hof-3")

    assert listing is not None
    assert listing.listing_visible is False


@pytest.mark.asyncio
async def test_ingest_url_handles_fetch_failure_gracefully(adapter):
    with respx.mock:
        respx.get("https://makler.example/unreachable").mock(side_effect=httpx.ConnectError("boom"))
        listing = await adapter.ingest_url("https://makler.example/unreachable")

    assert listing is None


@pytest.mark.asyncio
async def test_discover_yields_nothing(adapter, search_profile, sample_keywords):
    results = [item async for item in adapter.discover(search_profile, sample_keywords)]
    assert results == []
