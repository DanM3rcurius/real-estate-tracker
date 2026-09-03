"""CsvAdapter: German headers, messy values, rows without a URL are skipped."""

from __future__ import annotations

import pytest

from hofradar.sources.adapters.csv_adapter import CsvAdapter, parse_csv_text


def test_parse_csv_text_maps_german_headers_and_skips_rows_without_url(read_fixture):
    text = read_fixture("listings.csv")
    listings = parse_csv_text(text, source_key="csv_import")

    assert len(listings) == 2, "the row with no URL column value must be skipped"

    first = listings[0]
    assert first.url == "https://makler.example/hof-1"
    assert first.title == "Alter Bauernhof mit Stadel"
    assert first.price_raw == "450.000 €"
    assert first.land_raw == "5.000 m²"
    assert first.living_raw == "180 m²"
    assert first.town == "Feldkirchen-Westerham"
    assert first.postcode == "83620"
    assert first.year_raw == "1890"
    assert first.image_urls == ["https://img.example/a.jpg", "https://img.example/b.jpg"]
    assert first.extra == {"Notiz": "Sehr gepflegt, ruhige Lage"}

    second = listings[1]
    assert second.url == "https://makler.example/hof-2"
    assert second.price_raw == "VB"
    assert second.living_raw == "120 m²"
    assert second.town == "Vogtareuth"


def test_parse_csv_text_handles_empty_input():
    assert parse_csv_text("", source_key="csv_import") == []
    assert parse_csv_text("   \n  \n", source_key="csv_import") == []


def test_parse_csv_text_sniffs_semicolon_and_tab_delimiters():
    semicolon = "titel;preis;url\nHof A;300.000;https://example.test/a\n"
    listings = parse_csv_text(semicolon, source_key="csv_import")
    assert len(listings) == 1
    assert listings[0].title == "Hof A"
    assert listings[0].price_raw == "300.000"

    tab = "titel\tpreis\turl\nHof B\t250.000\thttps://example.test/b\n"
    listings = parse_csv_text(tab, source_key="csv_import")
    assert len(listings) == 1
    assert listings[0].title == "Hof B"


@pytest.mark.asyncio
async def test_discover_reads_configured_path(tmp_path, make_source_config, search_profile, sample_keywords):
    csv_path = tmp_path / "import.csv"
    csv_path.write_text(
        "titel,preis,url\nHof mit Scheune,399.000,https://example.test/hof\n", encoding="utf-8"
    )
    cfg = make_source_config(key="csv_import", adapter="csv", options={"path": str(csv_path)})
    adapter = CsvAdapter(cfg)

    results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert len(results) == 1
    assert results[0].url == "https://example.test/hof"
    assert results[0].title == "Hof mit Scheune"


@pytest.mark.asyncio
async def test_discover_without_path_yields_nothing(make_source_config, search_profile, sample_keywords):
    cfg = make_source_config(key="csv_import", adapter="csv", options={})
    adapter = CsvAdapter(cfg)

    results = [item async for item in adapter.discover(search_profile, sample_keywords)]
    assert results == []


@pytest.mark.asyncio
async def test_discover_logs_and_continues_on_unreadable_path(
    tmp_path, make_source_config, search_profile, sample_keywords
):
    cfg = make_source_config(
        key="csv_import", adapter="csv", options={"paths": [str(tmp_path / "missing.csv")]}
    )
    adapter = CsvAdapter(cfg)

    results = [item async for item in adapter.discover(search_profile, sample_keywords)]
    assert results == []


@pytest.mark.asyncio
async def test_fetch_detail_returns_none(make_source_config):
    cfg = make_source_config(key="csv_import", adapter="csv")
    adapter = CsvAdapter(cfg)
    assert await adapter.fetch_detail("https://example.test/anything") is None
