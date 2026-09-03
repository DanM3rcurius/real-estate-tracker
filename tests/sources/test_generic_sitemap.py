"""GenericSitemapAdapter: sitemap index resolution, URL filtering, max_pages cap."""

from __future__ import annotations

import httpx
import pytest
import respx

from hofradar.sources.adapters.generic_sitemap import GenericSitemapAdapter
from hofradar.sources.exceptions import SourceDiscoveryError

DETAIL_HTML = """<!DOCTYPE html>
<html><head><title>{title}</title></head>
<body><h1>{title}</h1><p>Preis: 300.000 €</p></body></html>
"""


@pytest.mark.asyncio
async def test_discover_resolves_sitemap_index_and_filters_by_pattern(
    make_source_config, search_profile, sample_keywords, read_fixture
):
    cfg = make_source_config(
        key="generic_sitemap",
        adapter="generic_sitemap",
        options={
            "sites": [
                {
                    "sitemap_url": "https://broker.example/sitemap-index.xml",
                    "pattern": r"/immobilien/",
                }
            ]
        },
    )
    adapter = GenericSitemapAdapter(cfg)

    with respx.mock:
        respx.get("https://broker.example/sitemap-index.xml").mock(
            return_value=httpx.Response(200, text=read_fixture("sitemap_index.xml"))
        )
        respx.get("https://broker.example/sitemap-listings.xml").mock(
            return_value=httpx.Response(200, text=read_fixture("sitemap_listings.xml"))
        )
        respx.get("https://broker.example/immobilien/hofstelle-feldkirchen").mock(
            return_value=httpx.Response(200, text=DETAIL_HTML.format(title="Hofstelle Feldkirchen"))
        )
        respx.get("https://broker.example/immobilien/resthof-vogtareuth").mock(
            return_value=httpx.Response(200, text=DETAIL_HTML.format(title="Resthof Vogtareuth"))
        )
        # The blog URL is deliberately left unmocked: if the pattern filter
        # fails to exclude it, respx raises and this test fails loudly.
        results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    urls = {r.url for r in results}
    assert urls == {
        "https://broker.example/immobilien/hofstelle-feldkirchen",
        "https://broker.example/immobilien/resthof-vogtareuth",
    }
    titles = {r.title for r in results}
    assert titles == {"Hofstelle Feldkirchen", "Resthof Vogtareuth"}


@pytest.mark.asyncio
async def test_max_pages_caps_the_number_of_detail_fetches(
    make_source_config, search_profile, sample_keywords, read_fixture
):
    cfg = make_source_config(
        key="generic_sitemap",
        adapter="generic_sitemap",
        options={
            "sites": ["https://broker.example/sitemap-listings.xml"],
            "max_pages": 1,
        },
    )
    adapter = GenericSitemapAdapter(cfg)

    with respx.mock:
        respx.get("https://broker.example/sitemap-listings.xml").mock(
            return_value=httpx.Response(200, text=read_fixture("sitemap_listings.xml"))
        )
        respx.get("https://broker.example/immobilien/hofstelle-feldkirchen").mock(
            return_value=httpx.Response(200, text=DETAIL_HTML.format(title="Hofstelle Feldkirchen"))
        )
        # Only the first URL should ever be fetched given max_pages=1; the
        # other two sitemap URLs are intentionally left unmocked.
        results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert len(results) == 1


@pytest.mark.asyncio
async def test_discover_without_sites_configured_yields_nothing(
    make_source_config, search_profile, sample_keywords
):
    cfg = make_source_config(key="generic_sitemap", adapter="generic_sitemap", options={})
    adapter = GenericSitemapAdapter(cfg)
    results = [item async for item in adapter.discover(search_profile, sample_keywords)]
    assert results == []


@pytest.mark.asyncio
async def test_discover_raises_when_sitemap_unreachable(
    make_source_config, search_profile, sample_keywords
):
    cfg = make_source_config(
        key="generic_sitemap",
        adapter="generic_sitemap",
        options={"sites": ["https://broker.example/sitemap-listings.xml"]},
    )
    adapter = GenericSitemapAdapter(cfg)

    with respx.mock:
        respx.get("https://broker.example/sitemap-listings.xml").mock(return_value=httpx.Response(500))
        with pytest.raises(SourceDiscoveryError):
            async for _ in adapter.discover(search_profile, sample_keywords):
                pass
