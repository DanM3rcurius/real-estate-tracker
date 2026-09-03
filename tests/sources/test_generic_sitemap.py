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


# --------------------------------------------------------------------------- #
# Invariant 4b: this adapter is role=primary and enumerates=True, so every way
# a run can fall short of a complete enumeration has to say so - otherwise
# mark_missing reads the gap as "the seller withdrew it". Latent only while
# options.sites is empty; the first site added makes a flaky detail page a
# false REMOVED.
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_a_failed_detail_fetch_marks_the_enumeration_incomplete(
    make_source_config, search_profile, sample_keywords, read_fixture
):
    """The dangerous case: a run that looks entirely normal, one listing short.

    The sitemap named the page, so it is still on offer; we simply never looked
    at it. Its absence from the observed set must not license "this is gone".
    """
    cfg = make_source_config(
        key="generic_sitemap",
        adapter="generic_sitemap",
        options={
            "sites": ["https://broker.example/sitemap-listings.xml"],
            "pattern": r"/immobilien/",
        },
    )
    adapter = GenericSitemapAdapter(cfg)

    with respx.mock:
        respx.get("https://broker.example/sitemap-listings.xml").mock(
            return_value=httpx.Response(200, text=read_fixture("sitemap_listings.xml"))
        )
        respx.get("https://broker.example/immobilien/hofstelle-feldkirchen").mock(
            return_value=httpx.Response(200, text=DETAIL_HTML.format(title="Hofstelle"))
        )
        # 404 is not retryable in PoliteClient, so this is fast and lands in
        # fetch_detail's own >=400 -> None path.
        respx.get("https://broker.example/immobilien/resthof-vogtareuth").mock(
            return_value=httpx.Response(404)
        )
        results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert len(results) == 1
    assert adapter.enumeration_complete is False
    assert adapter.can_prove_absence is False


@pytest.mark.asyncio
async def test_a_detail_fetch_that_raises_marks_the_enumeration_incomplete(
    make_source_config, search_profile, sample_keywords, read_fixture
):
    cfg = make_source_config(
        key="generic_sitemap",
        adapter="generic_sitemap",
        options={
            "sites": ["https://broker.example/sitemap-listings.xml"],
            "pattern": r"/immobilien/",
        },
    )
    adapter = GenericSitemapAdapter(cfg)

    with respx.mock:
        respx.get("https://broker.example/sitemap-listings.xml").mock(
            return_value=httpx.Response(200, text=read_fixture("sitemap_listings.xml"))
        )
        respx.get("https://broker.example/immobilien/hofstelle-feldkirchen").mock(
            side_effect=RuntimeError("boom")
        )
        respx.get("https://broker.example/immobilien/resthof-vogtareuth").mock(
            return_value=httpx.Response(200, text=DETAIL_HTML.format(title="Resthof"))
        )
        results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert len(results) == 1
    assert adapter.enumeration_complete is False


@pytest.mark.asyncio
async def test_every_sitemap_url_is_recorded_even_when_the_pattern_skips_it(
    make_source_config, search_profile, sample_keywords, read_fixture
):
    """The Denkmalbörse rule, applied here: a URL this run chose not to fetch
    is a routing decision, never a claim the site withdrew it. Recording it
    before the pattern filter is what lets ``pipeline.runner`` tell "skipped
    this run" apart from "the source stopped carrying it"."""
    cfg = make_source_config(
        key="generic_sitemap",
        adapter="generic_sitemap",
        options={
            "sites": ["https://broker.example/sitemap-listings.xml"],
            "pattern": r"/immobilien/",
        },
    )
    adapter = GenericSitemapAdapter(cfg)

    with respx.mock:
        respx.get("https://broker.example/sitemap-listings.xml").mock(
            return_value=httpx.Response(200, text=read_fixture("sitemap_listings.xml"))
        )
        respx.get(url__regex=r"https://broker\.example/immobilien/.+").mock(
            return_value=httpx.Response(200, text=DETAIL_HTML.format(title="Hof"))
        )
        [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert adapter.enumerated_urls == {
        "https://broker.example/immobilien/hofstelle-feldkirchen",
        "https://broker.example/immobilien/resthof-vogtareuth",
        "https://broker.example/blog/marktbericht-2026",
    }
    assert adapter.enumeration_complete is True


@pytest.mark.asyncio
async def test_discover_without_sites_configured_cannot_prove_absence(
    make_source_config, search_profile, sample_keywords
):
    """begin_enumeration() has to run before the empty-sites return, and the
    run has to declare itself incomplete: a source that searched nothing must
    not be able to tell mark_missing that everything else is gone."""
    cfg = make_source_config(key="generic_sitemap", adapter="generic_sitemap", options={})
    adapter = GenericSitemapAdapter(cfg)

    results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert results == []
    assert adapter.enumeration_complete is False
    assert adapter.can_prove_absence is False


@pytest.mark.asyncio
async def test_discover_abandoned_early_never_claims_a_complete_enumeration(
    make_source_config, search_profile, sample_keywords, read_fixture
):
    cfg = make_source_config(
        key="generic_sitemap",
        adapter="generic_sitemap",
        options={
            "sites": ["https://broker.example/sitemap-listings.xml"],
            "pattern": r"/immobilien/",
        },
    )
    adapter = GenericSitemapAdapter(cfg)

    with respx.mock:
        respx.get("https://broker.example/sitemap-listings.xml").mock(
            return_value=httpx.Response(200, text=read_fixture("sitemap_listings.xml"))
        )
        respx.get(url__regex=r"https://broker\.example/immobilien/.+").mock(
            return_value=httpx.Response(200, text=DETAIL_HTML.format(title="Hof"))
        )
        listings = adapter.discover(search_profile, sample_keywords)
        await anext(listings)
        await listings.aclose()

    assert adapter.enumeration_complete is False
