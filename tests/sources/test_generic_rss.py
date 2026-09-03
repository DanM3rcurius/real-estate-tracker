"""GenericRssAdapter: feedparser over configured feeds, fetch_detail enrichment."""

from __future__ import annotations

import httpx
import pytest
import respx

from hofradar.sources.adapters.generic_rss import GenericRssAdapter
from hofradar.sources.exceptions import SourceDiscoveryError


@pytest.mark.asyncio
async def test_discover_parses_feed_entries_and_skips_entry_without_link(
    make_source_config, search_profile, sample_keywords, read_fixture
):
    feed_xml = read_fixture("rss_feed.xml")
    cfg = make_source_config(
        key="generic_rss",
        adapter="generic_rss",
        options={"feeds": ["https://makler.example/feed.xml"]},
    )
    adapter = GenericRssAdapter(cfg)

    with respx.mock:
        respx.get("https://makler.example/feed.xml").mock(
            return_value=httpx.Response(200, text=feed_xml, headers={"Content-Type": "application/rss+xml"})
        )
        results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert len(results) == 1, "the entry with no <link> must be skipped"
    listing = results[0]
    assert listing.source_key == "generic_rss"
    assert listing.url == "https://makler.example/objekt/1"
    assert listing.title == "Resthof mit Scheune bei Feldkirchen"
    assert listing.external_id == "obj-1"
    assert listing.source_date_raw == "Mon, 01 Sep 2026 08:00:00 GMT"
    assert "Nebengebaeuden" in listing.description


@pytest.mark.asyncio
async def test_discover_without_feeds_configured_yields_nothing(
    make_source_config, search_profile, sample_keywords
):
    cfg = make_source_config(key="generic_rss", adapter="generic_rss", options={})
    adapter = GenericRssAdapter(cfg)
    results = [item async for item in adapter.discover(search_profile, sample_keywords)]
    assert results == []


@pytest.mark.asyncio
async def test_discover_one_bad_feed_does_not_abort_others(
    make_source_config, search_profile, sample_keywords, read_fixture
):
    feed_xml = read_fixture("rss_feed.xml")
    cfg = make_source_config(
        key="generic_rss",
        adapter="generic_rss",
        options={"feeds": ["https://broken.example/feed.xml", "https://makler.example/feed.xml"]},
    )
    adapter = GenericRssAdapter(cfg)

    with respx.mock:
        respx.get("https://broken.example/feed.xml").mock(return_value=httpx.Response(500))
        respx.get("https://makler.example/feed.xml").mock(return_value=httpx.Response(200, text=feed_xml))
        results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert len(results) == 1
    assert results[0].url == "https://makler.example/objekt/1"


@pytest.mark.asyncio
async def test_discover_raises_when_no_feed_is_readable_at_all(
    make_source_config, search_profile, sample_keywords
):
    cfg = make_source_config(
        key="generic_rss", adapter="generic_rss", options={"feeds": ["https://broken.example/feed.xml"]}
    )
    adapter = GenericRssAdapter(cfg)

    with respx.mock:
        respx.get("https://broken.example/feed.xml").mock(return_value=httpx.Response(500))
        with pytest.raises(SourceDiscoveryError):
            async for _ in adapter.discover(search_profile, sample_keywords):
                pass


@pytest.mark.asyncio
async def test_fetch_detail_enriches_from_entry_link(make_source_config, read_fixture):
    detail_html = read_fixture("detail_live.html")
    cfg = make_source_config(key="generic_rss", adapter="generic_rss")
    adapter = GenericRssAdapter(cfg)

    with respx.mock:
        respx.get("https://makler.example/objekt/1").mock(
            return_value=httpx.Response(200, text=detail_html)
        )
        listing = await adapter.fetch_detail("https://makler.example/objekt/1")

    assert listing is not None
    assert listing.title == "Hofstelle mit Scheune bei Feldkirchen-Westerham"
    assert listing.price_raw == "590.000 €"
