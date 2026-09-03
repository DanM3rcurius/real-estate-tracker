"""The adapter fetches; it does not parse German into typed facts.

Everything asserted here is about *which request would have been made* and
which stringy fields came back. Turning "auf Anfrage" into a PriceType is
hofradar.normalize's job, and testing it here would duplicate that contract.

The fixture this file reads (``tests/fixtures/html/denkmalboerse_object_005816.html``)
is a real page captured from ``www.blfd.bayern.de`` on 2026-09-03 (see the
provenance comment at the top of the fixture). Its assertions reflect what
``_htmlutil`` genuinely pulls out of that markup, not what a hand-written
fixture implied it would.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from hofradar.config import RadiusConfig
from hofradar.sources import get_adapter
from hofradar.sources.adapters.denkmalboerse import _town_from_title

BASE = "https://www.blfd.bayern.de"
DETAIL = f"{BASE}/information-service/denkmalboerse/objekte/005816/index.html"
FIXTURE_NAME = "denkmalboerse_object_005816.html"


@pytest.mark.asyncio
async def test_fetch_detail_requests_the_static_object_page(
    make_source_config, read_fixture
) -> None:
    adapter = get_adapter(
        make_source_config(key="denkmalboerse", adapter="denkmalboerse", base_url=BASE)
    )

    with respx.mock:
        route = respx.get(DETAIL).mock(
            return_value=httpx.Response(200, text=read_fixture(FIXTURE_NAME))
        )
        listing = await adapter.fetch_detail(DETAIL)

    assert route.called
    assert listing is not None
    assert listing.source_key == "denkmalboerse"
    assert listing.url == DETAIL
    assert listing.external_id == "005816"
    assert listing.http_status == 200
    # Contact is published in the exposé itself - not Chiffre, not via the Amt.
    assert listing.contact_kind == "private"
    # This page carries no og:title - only <title> and a matching <h1>. Title
    # extraction now depends on _htmlutil's <title> fallback, exercised here
    # for the first time against real BLfD markup.
    assert listing.title == "Kleinbauernhof in Altenstadt bei Schongau"
    # extract_labeled_fields picks up the plain "Kaufpreis:"/"Baujahr:" lines
    # from the Kurzinfo box even though the surrounding <p> tags are nested
    # (the real page is not well-formed HTML there).
    assert listing.price_raw == "auf Anfrage"
    assert listing.year_raw == "2. Hälfte 18. Jahrhundert"
    # "Grundstücksfläche:" matches the label map exactly. "Wohnfläche
    # (Bauernhaus):" and "Nutzfläche (Wirtschaftsteil):" carry a parenthetical
    # suffix BLfD's owner exposés routinely add for a Hofstelle's living vs.
    # working part - _htmlutil.extract_labeled_fields strips that suffix for
    # matching purposes when the base label is already a known key, so both
    # still resolve to living_raw / usable_raw. See test_htmlutil.py for the
    # focused coverage of that normalisation and its guard rail.
    assert listing.land_raw == "ca. 435 m²"
    assert listing.living_raw == "ca. 110 m²"
    assert listing.usable_raw == "ca. 112 m²"
    # The contact block (immo-inhalt, "Eigentümer des Anwesens", the mailto
    # target's visible text) is plain body text, so it survives into the
    # description like everything else on the page - there is no separate
    # contact field to check.
    assert "Eigentümer des Anwesens" in (listing.description or "")
    assert "hofstelle-bayern@web.de" in (listing.description or "")


@pytest.mark.asyncio
async def test_verify_reports_a_removed_object_as_gone(make_source_config) -> None:
    # Regression coverage for inherited behaviour, not new code: SourceAdapter
    # already implements verify() via _verify_impl. It is here because the
    # role gate (only primary/local may verify) is the thing most likely to
    # break silently if this source is ever reclassified.
    adapter = get_adapter(
        make_source_config(key="denkmalboerse", adapter="denkmalboerse", base_url=BASE)
    )

    with respx.mock:
        respx.get(DETAIL).mock(return_value=httpx.Response(404))
        still_live, status = await adapter.verify(DETAIL)

    assert still_live is False
    assert status == 404


@pytest.mark.asyncio
async def test_discover_skips_the_detail_fetch_for_an_out_of_radius_town(
    make_source_config, search_profile, sample_keywords
) -> None:
    adapter = get_adapter(
        make_source_config(key="denkmalboerse", adapter="denkmalboerse", base_url=BASE)
    )
    # "Nordhalben" is absent from this project's Upper-Bavaria-only gazetteer,
    # which would exercise the None branch instead of False and prove
    # nothing. "Neumarkt-Sankt Veit" is a real gazetteer entry (Landkreis
    # Muehldorf), ~71 km from the Westham origin - known and outside a 60 km
    # radius, the case this test is actually meant to cover.
    profile = search_profile.model_copy(update={"radius": RadiusConfig(air_km_max=60)})
    index = f"{BASE}/cgi-bin/fts_search_verkauf.pl"

    with respx.mock:
        respx.get(index).mock(
            return_value=httpx.Response(
                200,
                text=(
                    '<a href="/information-service/denkmalboerse/objekte/005759/index.html">'
                    "Pfarrhof in Neumarkt-Sankt Veit</a>"
                ),
            )
        )
        detail = respx.get(
            f"{BASE}/information-service/denkmalboerse/objekte/005759/index.html"
        ).mock(return_value=httpx.Response(200, text="<html></html>"))

        listings = [item async for item in adapter.discover(profile, sample_keywords)]

    assert listings == []
    assert not detail.called, "Neumarkt-Sankt Veit is 71km out; the page must not be fetched"


@pytest.mark.asyncio
async def test_discover_fetches_an_unknown_town_rather_than_discarding_it(
    make_source_config, search_profile, sample_keywords, read_fixture
) -> None:
    adapter = get_adapter(
        make_source_config(key="denkmalboerse", adapter="denkmalboerse", base_url=BASE)
    )
    index = f"{BASE}/cgi-bin/fts_search_verkauf.pl"

    with respx.mock:
        respx.get(index).mock(
            return_value=httpx.Response(
                200,
                text=(
                    '<a href="/information-service/denkmalboerse/objekte/007148/index.html">'
                    "Historische Hofstelle in Hinterdupfing</a>"
                ),
            )
        )
        detail = respx.get(
            f"{BASE}/information-service/denkmalboerse/objekte/007148/index.html"
        ).mock(return_value=httpx.Response(200, text=read_fixture(FIXTURE_NAME)))

        listings = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert detail.called, "an unknown town must fall through to the full path"
    assert len(listings) == 1


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        # A spaced dash sets off a district suffix.
        ("Historische Hofstelle in Reichenschwand - Leuzenberg", "Reichenschwand"),
        # A comma sets off a district/region, same as a spaced dash.
        ("Bauernhaus in Rosenheim, Oberbayern", "Rosenheim"),
        ("Hof in Bruckmühl, Ortsteil Götting", "Bruckmühl"),
        # An unspaced hyphen is part of the town's own name, not a separator.
        ("Pfarrhof in Neumarkt-Sankt Veit", "Neumarkt-Sankt Veit"),
        # No separator at all: the whole remainder is the town.
        ("Kleinbauernhof in Altenstadt bei Schongau", "Altenstadt bei Schongau"),
        ("Historische Hofstelle in Hinterdupfing", "Hinterdupfing"),
        # An em dash is recognised as a district separator too.
        ("Hofstelle in Bad Aibling — Mietraching", "Bad Aibling"),
    ],
)
def test_town_from_title_separates_the_town_from_a_district_suffix(
    title: str, expected: str
) -> None:
    assert _town_from_title(title) == expected


@pytest.mark.asyncio
async def test_discover_never_claims_a_complete_enumeration(
    make_source_config, search_profile, sample_keywords
) -> None:
    """Invariant 4b: enumerates=True (the class default) plus role=primary
    would make can_prove_absence True unless discover() says otherwise - and
    a bare GET against the search CGI has never been checked against a real
    response for pagination or a search-form-instead-of-results reply (see
    the OUTSTANDING note in docs/SOURCES.md). So this run's silence must
    never be readable as "everything else was removed", regardless of what
    the mocked response here looks like.
    """
    adapter = get_adapter(
        make_source_config(key="denkmalboerse", adapter="denkmalboerse", base_url=BASE)
    )
    index = f"{BASE}/cgi-bin/fts_search_verkauf.pl"

    with respx.mock:
        respx.get(index).mock(return_value=httpx.Response(200, text="<html></html>"))
        [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert adapter.enumeration_complete is False
    assert adapter.can_prove_absence is False
