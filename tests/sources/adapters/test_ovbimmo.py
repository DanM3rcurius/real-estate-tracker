"""The OVB regional portal adapter.

Four things worth pinning:

1. The six-character detail id is the external identifier (the title slug is
   cosmetic and changes when a broker rewrites the headline) - and it is
   read from the URL, never from the detail page's markup, so a broker
   rewriting the headline can never change what a listing dedupes against.
2. Discovery walks one faceted `/kaufen/{typ}/{ort}` URL per configured
   municipality/property-type combination.
3. A single search page carries a fraction of the total result count (the
   real capture used here: 20 of 186). Per invariant 4b, a walk that stops
   before exhausting `<link rel="next">` must never leave the enumeration
   flagged complete.
4. What `fetch_detail` genuinely pulls out of a real detail page: the title
   (og:title), the visible body text as description, and image URLs - but
   NOT the structured price/rooms/area fields, because
   `_htmlutil.extract_labeled_fields` looks for "Label: value" lines and
   this page's `eps-item` blocks render the value *before* the label, each
   on its own line once the body is flattened. See docs/SOURCES.md for the
   consequence.

Both fixtures used here are real captures, not hand-written approximations:
`ovbimmo_search_rosenheim.html` (`ovbimmo.de/kaufen/rosenheim-kreis`,
2026-09-03) and `ovbimmo_detail.html`
(`ovbimmo.de/immobilien/zweifamilienhaus-grosskarolinenfeld-grosser-sonniger-garten-H3N33B`,
2026-09-03) - see the provenance comments at the top of each. The captured
detail listing is a **broker** listing ("Provision für Käufer", Robert
Schlamp Immobilien e. K.), not a private Chiffre one - one capture is one
page, and `contact_kind` is deliberately never inferred from it (see the
module docstring in `ovbimmo.py`).
"""

from __future__ import annotations

import httpx
import pytest
import respx

from hofradar.sources import get_adapter

BASE = "https://ovbimmo.de"
SEARCH_FIXTURE = "ovbimmo_search_rosenheim.html"
DETAIL_FIXTURE = "ovbimmo_detail.html"
#: One real link out of the search fixture (slug + trailing 6-char code).
DETAIL_URL = (
    f"{BASE}/immobilien/interessante-wertanlage-bad-endorf-charmantes-"
    "apartment-mit-serioesem-mieter-GZJFXJ"
)
#: The real detail fixture's own URL (a different listing than DETAIL_URL -
#: the search fixture and the detail fixture are two independent captures).
REAL_DETAIL_URL = (
    f"{BASE}/immobilien/zweifamilienhaus-grosskarolinenfeld-"
    "grosser-sonniger-garten-H3N33B"
)
#: A minimal, deliberately generic detail-page stand-in: enough for respx to
#: return 200 with a body, without pretending to know the real markup shape.
STUB_DETAIL_HTML = "<html><head><title>Stub</title></head><body>stub</body></html>"
#: A synthetic *second* search page: no cards, no further <link rel="next">.
#: Used only to exercise the pagination loop's natural termination - it is
#: not offered as evidence of what a real page 2 looks like.
EMPTY_NEXT_PAGE_HTML = "<html><head><title>Seite 2</title></head><body></body></html>"


@pytest.fixture
def adapter(make_source_config):
    return get_adapter(
        make_source_config(
            key="ovbimmo",
            adapter="ovbimmo",
            role="local",
            base_url=BASE,
            options={
                "municipalities": ["rosenheim-kreis"],
                "property_types": ["haus"],
            },
        )
    )


@pytest.mark.asyncio
async def test_fetch_detail_extracts_external_id_from_the_url_not_the_page(adapter) -> None:
    with respx.mock:
        route = respx.get(DETAIL_URL).mock(
            return_value=httpx.Response(200, text=STUB_DETAIL_HTML)
        )
        listing = await adapter.fetch_detail(DETAIL_URL)

    assert route.called
    assert listing is not None
    assert listing.url == DETAIL_URL
    assert listing.external_id == "GZJFXJ"
    assert listing.source_key == "ovbimmo"


@pytest.mark.asyncio
async def test_fetch_detail_against_the_real_capture(adapter, read_fixture) -> None:
    """What `raw_listing_from_html` genuinely pulls out of a real OVB detail
    page - not what the dataLayer/Objektdaten *could* offer with a purpose-
    built parser, which does not exist yet (see the module docstring).
    """
    with respx.mock:
        respx.get(REAL_DETAIL_URL).mock(
            return_value=httpx.Response(200, text=read_fixture(DETAIL_FIXTURE))
        )
        listing = await adapter.fetch_detail(REAL_DETAIL_URL)

    assert listing is not None
    assert listing.source_key == "ovbimmo"
    assert listing.url == REAL_DETAIL_URL
    # The id comes from the URL, same as every other listing - not from the
    # dataLayer's matching "listing_id":"H3N33B", which is never read.
    assert listing.external_id == "H3N33B"
    # og:title is present on this page and _htmlutil prefers it.
    assert listing.title == "Zweifamilienhaus! Großkarolinenfeld! Großer sonniger Garten!"
    # og:image is present and preferred first among the collected image URLs.
    assert listing.image_urls[0] == (
        "https://ovbimmo.de/img-service/gZC35Wg4MG_oSvykbmlwf6IiVIzPrUXjlyPHXH7"
        "DofVXhQT6ut1ctc-TmHyjB3DO997K1l3rkCoGWtU0fiB9Cgw;scale=1300x680"
    )
    # The visible body text becomes the description, so the hidden_score
    # vocabulary and the price/rooms figures are IN there as plain text...
    assert "Provision für Käufer" in (listing.description or "")
    assert "690.000,00" in (listing.description or "")
    assert "Kaufpreis" in (listing.description or "")
    # ...but NOT as structured fields: extract_labeled_fields wants a single
    # "Label: value" line, and this page's eps-item blocks render the value
    # and its label on separate lines with the value FIRST
    # ("690.000,00 €\n\nKaufpreis", not "Kaufpreis: 690.000,00 €"). So the
    # generic extractor genuinely gets nothing here - this is not something
    # this task fixes; see docs/SOURCES.md.
    assert listing.price_raw is None
    assert listing.rooms_raw is None
    assert listing.living_raw is None
    assert listing.land_raw is None
    # The one thing this task must NOT do: guess contact_kind. The captured
    # page is a broker listing ("Provision für Käufer") whose own dataLayer
    # disagrees with itself (features includes "free_of_commission") - two
    # independent reasons never to derive this field from either source.
    assert listing.contact_kind is None


@pytest.mark.asyncio
async def test_fetch_detail_returns_none_for_a_missing_page(adapter) -> None:
    with respx.mock:
        respx.get(DETAIL_URL).mock(return_value=httpx.Response(404))
        listing = await adapter.fetch_detail(DETAIL_URL)

    assert listing is None


@pytest.mark.asyncio
async def test_a_gone_listing_is_reported_as_gone(adapter) -> None:
    # Regression coverage for inherited behaviour: SourceAdapter._verify_impl
    # already handles the 404/gone-marker path, exercised here because
    # role=local is what makes verify() reachable at all for this source.
    with respx.mock:
        respx.get(DETAIL_URL).mock(return_value=httpx.Response(404))
        still_live, status = await adapter.verify(DETAIL_URL)

    assert still_live is False
    assert status == 404


@pytest.mark.asyncio
async def test_discover_requests_the_faceted_url_for_the_configured_municipality(
    adapter, search_profile, sample_keywords, read_fixture
) -> None:
    search_url = f"{BASE}/kaufen/haus/rosenheim-kreis"

    with respx.mock:
        search = respx.get(search_url).mock(
            return_value=httpx.Response(200, text=read_fixture(SEARCH_FIXTURE))
        )
        respx.get(url__regex=rf"{BASE}/immobilien/.+").mock(
            return_value=httpx.Response(200, text=STUB_DETAIL_HTML)
        )
        # 186 results across ~10 pages: page 1 alone must not be followed to
        # page 2 in this test, so the facet's page cap is exhausted at 1.
        adapter.options["max_pages_per_search"] = 1

        listings = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert search.called
    # 20 unique /immobilien/<slug>-<6CHAR> links on the real capture.
    assert len(listings) == 20
    assert all(listing.source_key == "ovbimmo" for listing in listings)
    assert {listing.external_id for listing in listings} == {
        "H23XY8",
        "H3KTSM",
        "H23XY9",
        "GVB7G9",
        "H3N33B",
        "GYGPKL",
        "H3H2FR",
        "H3DBT9",
        "H2RHF8",
        "H2RH9W",
        "GZJFXJ",
        "H23M2M",
        "H39BPD",
        "GYWTR8",
        "GYZMGH",
        "GVL8NL",
        "H3P4M8",
        "H3K7ZB",
        "H23KWK",
        "H37PRT",
    }


@pytest.mark.asyncio
async def test_discover_over_the_real_fixture_never_claims_a_complete_enumeration_when_capped(
    adapter, search_profile, sample_keywords, read_fixture
) -> None:
    """Invariant 4b: the real fixture's own <link rel="next"> proves more
    results exist (186 total, 20 on this page). A page cap of 1 must stop the
    walk before that trail runs out, and the run must know it.
    """
    search_url = f"{BASE}/kaufen/haus/rosenheim-kreis"
    adapter.options["max_pages_per_search"] = 1

    with respx.mock:
        respx.get(search_url).mock(
            return_value=httpx.Response(200, text=read_fixture(SEARCH_FIXTURE))
        )
        # page=2 is deliberately left unmocked: if the cap failed to stop the
        # walk, respx would raise on the unmatched request.
        respx.get(url__regex=rf"{BASE}/immobilien/.+").mock(
            return_value=httpx.Response(200, text=STUB_DETAIL_HTML)
        )

        [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert adapter.enumeration_complete is False
    assert adapter.can_prove_absence is False


@pytest.mark.asyncio
async def test_discover_follows_pagination_until_no_next_link_remains(
    adapter, search_profile, sample_keywords, read_fixture
) -> None:
    """With a cap large enough to not bind, the walk follows <link rel="next">
    to page 2 and stops there because page 2 carries no further next link -
    leaving the enumeration complete rather than truncated.
    """
    search_url = f"{BASE}/kaufen/haus/rosenheim-kreis"
    page_2_url = f"{BASE}/kaufen/rosenheim-kreis?page=2"

    with respx.mock:
        page_1 = respx.get(search_url).mock(
            return_value=httpx.Response(200, text=read_fixture(SEARCH_FIXTURE))
        )
        page_2 = respx.get(page_2_url).mock(
            return_value=httpx.Response(200, text=EMPTY_NEXT_PAGE_HTML)
        )
        respx.get(url__regex=rf"{BASE}/immobilien/.+").mock(
            return_value=httpx.Response(200, text=STUB_DETAIL_HTML)
        )

        listings = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert page_1.called
    assert page_2.called
    assert len(listings) == 20
    assert adapter.enumeration_complete is True
    assert adapter.can_prove_absence is True


@pytest.mark.asyncio
async def test_discover_marks_incomplete_when_a_search_page_errors(
    adapter, search_profile, sample_keywords
) -> None:
    """The >=400 branch in `_walk_search` - untested until now. 404 (not a
    retryable status in PoliteClient) keeps this test fast and deterministic.
    """
    search_url = f"{BASE}/kaufen/haus/rosenheim-kreis"

    with respx.mock:
        respx.get(search_url).mock(return_value=httpx.Response(404))

        listings = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert listings == []
    assert adapter.enumeration_complete is False
    assert adapter.can_prove_absence is False


@pytest.mark.asyncio
async def test_discover_marks_incomplete_when_a_search_fetch_raises(
    adapter, search_profile, sample_keywords
) -> None:
    """The transport-exception branch in `_walk_search` - untested until now.
    A plain exception (not httpx.TransportError) keeps PoliteClient's own
    retry/backoff out of this test, which is about `_walk_search`'s except
    clause, not PoliteClient's retry policy.
    """
    search_url = f"{BASE}/kaufen/haus/rosenheim-kreis"

    with respx.mock:
        respx.get(search_url).mock(side_effect=RuntimeError("boom"))

        listings = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert listings == []
    assert adapter.enumeration_complete is False
    assert adapter.can_prove_absence is False


@pytest.mark.asyncio
async def test_discover_marks_incomplete_when_a_listed_id_fails_to_fetch(
    adapter, search_profile, sample_keywords, read_fixture
) -> None:
    """A listed id whose own detail fetch fails must not silently shrink the
    observed set: it was seen on the search page but never actually looked
    at, so its absence from the results must not license 'this is gone'.
    Concrete failure mode this guards: a property first observed 3 days ago
    (inside listing_ttl_days) would otherwise be read as fully enumerated
    and, via mark_missing, misclassified REMOVED rather than left alone.

    Deliberately isolated from the page-cap branch: the real search fixture
    itself carries a `<link rel="next">` to page 2, so a low
    `max_pages_per_search` would ALSO mark the enumeration incomplete for an
    entirely unrelated reason - making this test pass regardless of whether
    the detail-fetch-failure branch does anything at all. Page 2 here is a
    terminal page (no cards, no further `rel="next"`), the cap is left at
    its generous default, and the walk ends cleanly on its own; the only
    thing left standing that can mark the enumeration incomplete is the
    fetch_detail failure this test is actually about.
    """
    search_url = f"{BASE}/kaufen/haus/rosenheim-kreis"
    page_2_url = f"{BASE}/kaufen/rosenheim-kreis?page=2"

    with respx.mock:
        respx.get(search_url).mock(
            return_value=httpx.Response(200, text=read_fixture(SEARCH_FIXTURE))
        )
        respx.get(page_2_url).mock(return_value=httpx.Response(200, text=EMPTY_NEXT_PAGE_HTML))
        # One specific listed id 404s; every other detail fetch succeeds
        # normally. 404 is not a retryable status in PoliteClient, so this
        # stays fast and exercises fetch_detail's own >=400 -> None path.
        respx.get(
            f"{BASE}/immobilien/interessante-wertanlage-bad-endorf-charmantes-"
            "apartment-mit-serioesem-mieter-GZJFXJ"
        ).mock(return_value=httpx.Response(404))
        respx.get(url__regex=rf"{BASE}/immobilien/.+").mock(
            return_value=httpx.Response(200, text=STUB_DETAIL_HTML)
        )

        listings = [item async for item in adapter.discover(search_profile, sample_keywords)]

    # 20 ids on the page, 1 failed - 19 make it through, not silently 20.
    assert len(listings) == 19
    assert "GZJFXJ" not in {listing.external_id for listing in listings}
    # The walk reached page 2, which had no further rel="next" - so the page
    # cap (default 5) was never in play. Only the failed detail fetch can
    # have caused this.
    assert adapter.enumeration_complete is False
    assert adapter.can_prove_absence is False


@pytest.mark.asyncio
async def test_discover_without_municipalities_configured_yields_nothing_and_is_incomplete(
    make_source_config, search_profile, sample_keywords
) -> None:
    adapter = get_adapter(
        make_source_config(
            key="ovbimmo", adapter="ovbimmo", role="local", base_url=BASE, options={}
        )
    )

    listings = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert listings == []
    assert adapter.enumeration_complete is False
