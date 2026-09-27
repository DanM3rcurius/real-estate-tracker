"""GenericRssAdapter: feedparser over configured feeds, fetch_detail enrichment."""

from __future__ import annotations

import httpx
import pytest
import respx

from hofradar.sources.adapters.generic_rss import DETAIL_UNREADABLE_WARNING, GenericRssAdapter
from hofradar.sources.exceptions import SourceDiscoveryError
from tests.sources.conftest import FIXTURES_DIR


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


UTILITY_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Makler Feldkirchen</title>
  <link>https://makler.example</link>
  <description>Aktuelle Angebote</description>
  <item>
    <title>Resthof mit Scheune bei Feldkirchen</title>
    <link>https://makler.example/objekt/1</link>
    <guid>obj-1</guid>
    <description>Schoener Resthof mit Nebengebaeuden.</description>
  </item>
  <item>
    <title>Merkliste</title>
    <link>https://makler.example/merkliste</link>
    <guid>merkliste</guid>
    <description>Ihre gemerkten Objekte.</description>
  </item>
</channel></rss>
"""


@pytest.mark.asyncio
async def test_a_utility_entry_is_skipped_without_changing_what_the_feed_proves(
    make_source_config, search_profile, sample_keywords
):
    """Issue #10: a feed that syndicates the site's own bookmark page must not
    turn it into a property. A feed proves nothing by its silence either way
    (``enumerates`` is False), and skipping an entry must not change that.
    """
    cfg = make_source_config(
        key="generic_rss",
        adapter="generic_rss",
        options={"feeds": ["https://makler.example/feed.xml"]},
    )
    adapter = GenericRssAdapter(cfg)

    with respx.mock:
        respx.get("https://makler.example/feed.xml").mock(
            return_value=httpx.Response(
                200, text=UTILITY_FEED, headers={"Content-Type": "application/rss+xml"}
            )
        )
        results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert {r.url for r in results} == {"https://makler.example/objekt/1"}
    assert adapter.can_prove_absence is False



def _feed_route() -> None:
    feed_xml = (FIXTURES_DIR / "rss_feed.xml").read_text(encoding="utf-8")
    respx.get("https://makler.example/feed.xml").mock(
        return_value=httpx.Response(
            200, text=feed_xml, headers={"Content-Type": "application/rss+xml"}
        )
    )

@pytest.mark.asyncio
async def test_discover_follows_each_entry_to_its_detail_page(
    make_source_config, search_profile, sample_keywords, read_fixture
):
    """A feed entry is a teaser: the price and the areas live on the page it
    links to. Without this follow-up every ovbimmo.de property found by feed
    showed "k. A." for all of them.
    """
    cfg = make_source_config(
        key="generic_rss",
        adapter="generic_rss",
        options={"feeds": ["https://makler.example/feed.xml"]},
    )
    adapter = GenericRssAdapter(cfg)

    with respx.mock:
        _feed_route()
        detail = respx.get("https://makler.example/objekt/1").mock(
            return_value=httpx.Response(200, text=read_fixture("detail_live.html"))
        )
        results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert detail.called
    [listing] = results
    assert listing.price_raw == "590.000 €"
    assert listing.living_raw == "210 m²"
    assert listing.land_raw == "6.500 m²"
    assert listing.year_raw == "1902"
    assert listing.http_status == 200
    assert listing.warnings == []
    # The feed keeps the identity and the headline it announced.
    assert listing.external_id == "obj-1"
    assert listing.title == "Resthof mit Scheune bei Feldkirchen"
    assert listing.source_date_raw == "Mon, 01 Sep 2026 08:00:00 GMT"
    # The page's full text replaces the teaser.
    assert "Kaufpreis" in listing.description


@pytest.mark.asyncio
async def test_an_unreadable_detail_page_keeps_the_teaser_and_says_so(
    make_source_config, search_profile, sample_keywords
):
    cfg = make_source_config(
        key="generic_rss",
        adapter="generic_rss",
        options={"feeds": ["https://makler.example/feed.xml"]},
    )
    adapter = GenericRssAdapter(cfg)

    with respx.mock:
        _feed_route()
        respx.get("https://makler.example/objekt/1").mock(return_value=httpx.Response(503))
        results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    [listing] = results
    assert listing.price_raw is None
    assert "Nebengebaeuden" in listing.description
    assert listing.warnings == [DETAIL_UNREADABLE_WARNING]


@pytest.mark.asyncio
async def test_fetch_detail_false_reads_the_feed_alone(
    make_source_config, search_profile, sample_keywords
):
    cfg = make_source_config(
        key="generic_rss",
        adapter="generic_rss",
        options={"feeds": ["https://makler.example/feed.xml"], "fetch_detail": False},
    )
    adapter = GenericRssAdapter(cfg)

    with respx.mock:
        _feed_route()
        detail = respx.get("https://makler.example/objekt/1")
        results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert not detail.called
    [listing] = results
    assert listing.warnings == []
