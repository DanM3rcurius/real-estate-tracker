"""ZvgAdapter: search POST + defensive results-table parsing against a fixture."""

from __future__ import annotations

import httpx
import pytest
import respx

from hofradar.sources.adapters.zvg import ZvgAdapter, parse_zvg_results
from hofradar.sources.exceptions import SourceDiscoveryError


def test_parse_zvg_results_extracts_fields_and_tolerates_missing_cells(read_fixture):
    html = read_fixture("zvg_results.html")
    listings = parse_zvg_results(html, source_key="zvg_bayern", base_url="https://www.zvg-portal.de")

    assert len(listings) == 2

    first = listings[0]
    assert first.external_id == "12 K 34/26"
    assert first.price_raw == "450.000,00 EUR"
    assert first.location_raw == "Amtsgericht Rosenheim, 83620 Feldkirchen-Westerham"
    assert first.url == "https://www.zvg-portal.de/objekt.php?id=1001"
    assert "Scheune" in first.title
    assert first.extra["is_foreclosure"] is True
    assert first.extra["versteigerungstermin"] == "15.10.2026, 10:00 Uhr, Saal 12"

    second = listings[1]
    assert second.external_id == "07 K 09/26"
    assert second.price_raw == "612.500,00 EUR"
    # This row's fixture has no Versteigerungstermin/Ort cells at all -
    # the parser must not choke on the missing columns.
    assert second.location_raw == "Amtsgericht Miesbach"
    assert "versteigerungstermin" not in second.extra


def test_parse_zvg_results_ignores_unrelated_tables():
    html = """
    <table><thead><tr><th>Name</th><th>Wert</th></tr></thead>
    <tbody><tr><td>x</td><td>1</td></tr></tbody></table>
    """
    assert parse_zvg_results(html, source_key="zvg_bayern") == []


def test_parse_zvg_results_falls_back_to_synthesised_url_without_a_link():
    html = """
    <table><thead><tr><th>Aktenzeichen</th><th>Verkehrswert</th></tr></thead>
    <tbody><tr><td>3 K 1/26</td><td>100.000 EUR</td></tr></tbody></table>
    """
    listings = parse_zvg_results(html, source_key="zvg_bayern", base_url="https://www.zvg-portal.de")
    assert len(listings) == 1
    assert listings[0].url == "https://www.zvg-portal.de/index.php?az=3%20K%201/26"


@pytest.mark.asyncio
async def test_discover_posts_search_and_yields_parsed_rows(
    make_source_config, search_profile, sample_keywords, read_fixture
):
    cfg = make_source_config(
        key="zvg_bayern",
        adapter="zvg",
        role="primary",
        base_url="https://www.zvg-portal.de",
        options={"land_abk": "by"},
    )
    adapter = ZvgAdapter(cfg)

    with respx.mock:
        route = respx.post("https://www.zvg-portal.de/index.php").mock(
            return_value=httpx.Response(200, text=read_fixture("zvg_results.html"))
        )
        results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert route.call_count == 1
    assert len(results) == 2
    assert all(r.extra.get("is_foreclosure") is True for r in results)


@pytest.mark.asyncio
async def test_discover_raises_clearly_when_search_request_fails(
    make_source_config, search_profile, sample_keywords, monkeypatch
):
    async def fake_sleep(_delay: float) -> None:
        return None

    monkeypatch.setattr("hofradar.sources.base.asyncio.sleep", fake_sleep)

    cfg = make_source_config(
        key="zvg_bayern", adapter="zvg", role="primary", base_url="https://www.zvg-portal.de"
    )
    adapter = ZvgAdapter(cfg)

    with respx.mock:
        respx.post("https://www.zvg-portal.de/index.php").mock(return_value=httpx.Response(503))
        with pytest.raises(SourceDiscoveryError):
            async for _ in adapter.discover(search_profile, sample_keywords):
                pass


@pytest.mark.asyncio
async def test_fetch_detail_marks_is_foreclosure(make_source_config, read_fixture):
    cfg = make_source_config(
        key="zvg_bayern", adapter="zvg", role="primary", base_url="https://www.zvg-portal.de"
    )
    adapter = ZvgAdapter(cfg)

    with respx.mock:
        respx.get("https://www.zvg-portal.de/objekt.php?id=1001").mock(
            return_value=httpx.Response(200, text=read_fixture("detail_live.html"))
        )
        listing = await adapter.fetch_detail("https://www.zvg-portal.de/objekt.php?id=1001")

    assert listing is not None
    assert listing.extra["is_foreclosure"] is True
