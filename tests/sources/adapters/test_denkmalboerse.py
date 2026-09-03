"""The adapter fetches; it does not parse German into typed facts.

Everything asserted here is about *which request would have been made* and
which stringy fields came back. Turning "auf Anfrage" into a PriceType is
hofradar.normalize's job, and testing it here would duplicate that contract.

Two fixtures this file reads are real captures, not hand-written HTML:

- ``tests/fixtures/html/denkmalboerse_object_005816.html`` - one object's
  detail page, captured from ``www.blfd.bayern.de`` on 2026-09-03.
- ``tests/fixtures/html/denkmalboerse_search_cgi.html`` - the search CGI's
  full response, captured the same day: 237 rows across all seven Bavarian
  Regierungsbezirke, no pagination.

Assertions against the search-CGI fixture use real object ids and real town
names looked up from that capture, not stand-ins - see the comment on each
test for how the specific id was chosen.
"""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from hofradar.config import RadiusConfig
from hofradar.sources import get_adapter
from hofradar.sources.adapters.denkmalboerse import _town_from_title
from hofradar.sources.exceptions import SourceDiscoveryError

BASE = "https://www.blfd.bayern.de"
DETAIL = f"{BASE}/information-service/denkmalboerse/objekte/005816/index.html"
FIXTURE_NAME = "denkmalboerse_object_005816.html"
INDEX = f"{BASE}/cgi-bin/fts_search_verkauf.pl"
SEARCH_FIXTURE_NAME = "denkmalboerse_search_cgi.html"
#: Matches any object detail URL under BASE - used as a respx catch-all so a
#: real 43- or 237-row fixture can be walked without registering one route per
#: object id.
DETAIL_URL_RE = re.compile(
    rf"{re.escape(BASE)}/information-service/denkmalboerse/objekte/\d{{6}}/index\.html"
)


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
    make_source_config, search_profile, sample_keywords, read_fixture
) -> None:
    # Object 007505, "Stadthaus in Mühldorf a. Inn", is a real Oberbayern row
    # whose address line ("84453 Mühldorf am Inn") the gazetteer resolves to
    # "Muehldorf am Inn", 63.4km from the profile origin - inside the
    # default 80km radius, so narrow it to 60km to push this one object out,
    # the same technique the pre-real-fixture version of this test used to
    # push "Neumarkt-Sankt Veit" (71km) out.
    adapter = get_adapter(
        make_source_config(key="denkmalboerse", adapter="denkmalboerse", base_url=BASE)
    )
    profile = search_profile.model_copy(update={"radius": RadiusConfig(air_km_max=60)})
    out_of_radius_detail = f"{BASE}/information-service/denkmalboerse/objekte/007505/index.html"

    with respx.mock:
        respx.get(INDEX).mock(
            return_value=httpx.Response(200, text=read_fixture(SEARCH_FIXTURE_NAME))
        )
        detail = respx.get(out_of_radius_detail).mock(
            return_value=httpx.Response(200, text="<html></html>")
        )
        # Every other row that survives the Bezirk and radius gates at 60km -
        # a respx catch-all so this test does not need to enumerate them.
        respx.route(url__regex=DETAIL_URL_RE.pattern).mock(
            return_value=httpx.Response(200, text="<html><body>Objekt</body></html>")
        )

        [item async for item in adapter.discover(profile, sample_keywords)]

    assert not detail.called, "Mühldorf am Inn is 63.4km out at a 60km radius; must not be fetched"


@pytest.mark.asyncio
async def test_discover_fetches_an_unknown_town_rather_than_discarding_it(
    make_source_config, search_profile, sample_keywords, read_fixture
) -> None:
    # Object 009199, "Attraktives Ackerbürgerhaus in Ingolstadt", is a real
    # Oberbayern row ("85049 Ingolstadt") the bundled gazetteer has never
    # heard of - one of the 32-of-43 unknown-town Oberbayern rows
    # docs/SOURCES.md records. It must still reach fetch_detail.
    adapter = get_adapter(
        make_source_config(key="denkmalboerse", adapter="denkmalboerse", base_url=BASE)
    )
    unknown_town_detail = f"{BASE}/information-service/denkmalboerse/objekte/009199/index.html"

    with respx.mock:
        respx.get(INDEX).mock(
            return_value=httpx.Response(200, text=read_fixture(SEARCH_FIXTURE_NAME))
        )
        detail = respx.get(unknown_town_detail).mock(
            return_value=httpx.Response(200, text=read_fixture(FIXTURE_NAME))
        )
        # The other 97 in-scope rows (default scope: Oberbayern, Nieder-
        # bayern, Schwaben - 98 rows total) also fall through at this
        # profile's default radius (the gazetteer skips none of them either).
        others = respx.route(url__regex=DETAIL_URL_RE.pattern).mock(
            return_value=httpx.Response(200, text="<html><body>Objekt</body></html>")
        )

        listings = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert detail.called, "an unknown town must fall through to the full path"
    assert any(item.external_id == "009199" for item in listings)
    assert others.call_count == 97


@pytest.mark.asyncio
async def test_discover_skips_detail_fetches_for_out_of_scope_regierungsbezirk(
    make_source_config, search_profile, sample_keywords, read_fixture
) -> None:
    """The real capture carries 237 rows across all seven Regierungsbezirke;
    98 are Oberbayern (43), Niederbayern (27) or Schwaben (28) - the default
    in-scope set, tied to this profile's 80km radius (see
    DEFAULT_IN_SCOPE_REGIERUNGSBEZIRKE). The Bezirk column - checked before
    the gazetteer - must skip the other 139 with certainty, which is the
    entire reason it runs first (see the adapter's module docstring).
    """
    adapter = get_adapter(
        make_source_config(key="denkmalboerse", adapter="denkmalboerse", base_url=BASE)
    )

    with respx.mock:
        respx.get(INDEX).mock(
            return_value=httpx.Response(200, text=read_fixture(SEARCH_FIXTURE_NAME))
        )
        details = respx.route(url__regex=DETAIL_URL_RE.pattern).mock(
            return_value=httpx.Response(200, text="<html><body>Objekt</body></html>")
        )

        listings = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert details.call_count == 98, "only the 98 in-scope-Bezirk rows should reach fetch_detail"
    assert len(listings) == 98


@pytest.mark.asyncio
async def test_discover_regierungsbezirk_option_overrides_the_default_scope(
    make_source_config, search_profile, sample_keywords, read_fixture
) -> None:
    """options.regierungsbezirke replaces the Oberbayern default outright -
    an operator who wants a different or wider footprint is not stuck with it.
    """
    adapter = get_adapter(
        make_source_config(
            key="denkmalboerse",
            adapter="denkmalboerse",
            base_url=BASE,
            options={"regierungsbezirke": ["Mittelfranken"]},
        )
    )

    with respx.mock:
        respx.get(INDEX).mock(
            return_value=httpx.Response(200, text=read_fixture(SEARCH_FIXTURE_NAME))
        )
        details = respx.route(url__regex=DETAIL_URL_RE.pattern).mock(
            return_value=httpx.Response(200, text="<html><body>Objekt</body></html>")
        )

        listings = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert details.call_count == 38, "the real capture carries 38 Mittelfranken rows"
    assert len(listings) == 38


@pytest.mark.asyncio
async def test_discover_regierungsbezirk_option_is_case_insensitive(
    make_source_config, search_profile, sample_keywords, read_fixture
) -> None:
    """An operator typo like "oberbayern" must still match the row value
    "Oberbayern" - the alternative is a filter that looks configured but
    matches nothing, silently skipping every row in Bavaria, which is exactly
    what a pre-filter may never do.
    """
    adapter = get_adapter(
        make_source_config(
            key="denkmalboerse",
            adapter="denkmalboerse",
            base_url=BASE,
            options={"regierungsbezirke": ["oberbayern"]},
        )
    )

    with respx.mock:
        respx.get(INDEX).mock(
            return_value=httpx.Response(200, text=read_fixture(SEARCH_FIXTURE_NAME))
        )
        details = respx.route(url__regex=DETAIL_URL_RE.pattern).mock(
            return_value=httpx.Response(200, text="<html><body>Objekt</body></html>")
        )

        [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert details.call_count == 43, "case must not matter: still just the Oberbayern rows"


@pytest.mark.asyncio
async def test_discover_regierungsbezirk_empty_option_means_nothing_in_scope(
    make_source_config, search_profile, sample_keywords, read_fixture
) -> None:
    """An explicit empty list is a deliberate "nothing in scope", distinct
    from the key being absent entirely (which means "use the default").
    """
    adapter = get_adapter(
        make_source_config(
            key="denkmalboerse",
            adapter="denkmalboerse",
            base_url=BASE,
            options={"regierungsbezirke": []},
        )
    )

    with respx.mock:
        respx.get(INDEX).mock(
            return_value=httpx.Response(200, text=read_fixture(SEARCH_FIXTURE_NAME))
        )
        details = respx.route(url__regex=DETAIL_URL_RE.pattern).mock(
            return_value=httpx.Response(200, text="<html><body>Objekt</body></html>")
        )

        listings = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert details.call_count == 0
    assert listings == []


@pytest.mark.asyncio
async def test_discover_regierungsbezirk_garbage_option_falls_back_to_default(
    make_source_config, search_profile, sample_keywords, read_fixture
) -> None:
    """A configured value that matches none of Bavaria's seven Bezirke at
    all (not a case variant, just wrong) must not silently zero the source's
    yield - it falls back to the default rather than reject every property
    on a config nobody can act on.
    """
    adapter = get_adapter(
        make_source_config(
            key="denkmalboerse",
            adapter="denkmalboerse",
            base_url=BASE,
            options={"regierungsbezirke": ["Nordrhein-Westfalen"]},
        )
    )

    with respx.mock:
        respx.get(INDEX).mock(
            return_value=httpx.Response(200, text=read_fixture(SEARCH_FIXTURE_NAME))
        )
        details = respx.route(url__regex=DETAIL_URL_RE.pattern).mock(
            return_value=httpx.Response(200, text="<html><body>Objekt</body></html>")
        )

        [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert details.call_count == 98, "an unrecognisable override falls back to the default scope"


@pytest.mark.asyncio
async def test_discover_fetches_a_row_with_an_unrecognised_regierungsbezirk(
    make_source_config, search_profile, sample_keywords
) -> None:
    """A pre-filter may only ever save a fetch, never reject a property. A
    Bezirk column this adapter cannot recognise as one of Bavaria's seven -
    empty, or an unexpected value a future template change might introduce -
    must fall through and fetch, exactly like a gazetteer-unknown town does.
    Hand-written rather than drawn from the real fixture: no real row carries
    either value, which is the point being tested.
    """
    adapter = get_adapter(
        make_source_config(key="denkmalboerse", adapter="denkmalboerse", base_url=BASE)
    )
    empty_bezirk_detail = f"{BASE}/information-service/denkmalboerse/objekte/000001/index.html"
    unexpected_bezirk_detail = f"{BASE}/information-service/denkmalboerse/objekte/000002/index.html"
    index_html = """
        <table><tbody>
        <tr>
          <td>Kaufpreis: VB</td>
          <td><a href="/information-service/denkmalboerse/objekte/000001/index.html">
            Hof in Nirgendwo</a><p>00000 Nirgendwo</p></td>
          <td></td>
        </tr>
        <tr>
          <td>Kaufpreis: VB</td>
          <td><a href="/information-service/denkmalboerse/objekte/000002/index.html">
            Hof in Nirgendwo</a><p>00000 Nirgendwo</p></td>
          <td>Baden-Württemberg</td>
        </tr>
        </tbody></table>
    """

    with respx.mock:
        respx.get(INDEX).mock(return_value=httpx.Response(200, text=index_html))
        empty_bezirk = respx.get(empty_bezirk_detail).mock(
            return_value=httpx.Response(200, text="<html></html>")
        )
        unexpected_bezirk = respx.get(unexpected_bezirk_detail).mock(
            return_value=httpx.Response(200, text="<html></html>")
        )

        [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert empty_bezirk.called, "an empty Regierungsbezirk must fall through and fetch"
    assert unexpected_bezirk.called, "an unrecognised Regierungsbezirk must fall through and fetch"


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
async def test_discover_leaves_enumeration_complete_after_a_healthy_walk(
    make_source_config, search_profile, sample_keywords, read_fixture
) -> None:
    """Invariant 4b: a genuinely complete walk - every row parsed, every
    detail fetch that was attempted succeeded - is the one case where this
    run's silence about anything else may be read as "everything else is
    gone". Exercised over the real 237-row capture, not a stand-in.
    """
    adapter = get_adapter(
        make_source_config(key="denkmalboerse", adapter="denkmalboerse", base_url=BASE)
    )

    with respx.mock:
        respx.get(INDEX).mock(
            return_value=httpx.Response(200, text=read_fixture(SEARCH_FIXTURE_NAME))
        )
        respx.route(url__regex=DETAIL_URL_RE.pattern).mock(
            return_value=httpx.Response(200, text="<html><body>Objekt</body></html>")
        )

        [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert adapter.enumeration_complete is True
    assert adapter.can_prove_absence is True


@pytest.mark.asyncio
async def test_discover_marks_enumeration_incomplete_when_no_object_rows_parse(
    make_source_config, search_profile, sample_keywords
) -> None:
    """A template change that broke every selector above would look exactly
    like a zero-row parse from here - it must never be silently read as "the
    catalogue is genuinely empty this run".
    """
    adapter = get_adapter(
        make_source_config(key="denkmalboerse", adapter="denkmalboerse", base_url=BASE)
    )

    with respx.mock:
        respx.get(INDEX).mock(return_value=httpx.Response(200, text="<html></html>"))
        [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert adapter.enumeration_complete is False
    assert adapter.can_prove_absence is False


@pytest.mark.asyncio
async def test_discover_marks_enumeration_incomplete_when_not_fully_consumed(
    make_source_config, search_profile, sample_keywords, read_fixture
) -> None:
    """A consumer that stops draining early must not leave
    ``can_prove_absence`` true for a walk it never actually finished.
    ``hofradar.pipeline.runner`` always drains discover() fully today, so
    this is latent rather than an observed failure - but the adapter's own
    honesty must not depend on that being true forever, hence the
    ``finally`` in discover() this pins.
    """
    adapter = get_adapter(
        make_source_config(key="denkmalboerse", adapter="denkmalboerse", base_url=BASE)
    )

    with respx.mock:
        respx.get(INDEX).mock(
            return_value=httpx.Response(200, text=read_fixture(SEARCH_FIXTURE_NAME))
        )
        respx.route(url__regex=DETAIL_URL_RE.pattern).mock(
            return_value=httpx.Response(200, text="<html><body>Objekt</body></html>")
        )

        gen = adapter.discover(search_profile, sample_keywords)
        drained = 0
        async for _item in gen:
            drained += 1
            if drained >= 3:
                break
        await gen.aclose()

    assert drained == 3
    assert adapter.enumeration_complete is False
    assert adapter.can_prove_absence is False


@pytest.mark.asyncio
async def test_discover_marks_enumeration_incomplete_on_a_transport_error(
    make_source_config, search_profile, sample_keywords
) -> None:
    """The initial GET can fail outright (DNS, a reset connection) rather
    than come back with an HTTP error status - a distinct branch from the
    non-200 case below.
    """
    adapter = get_adapter(
        make_source_config(key="denkmalboerse", adapter="denkmalboerse", base_url=BASE)
    )

    with respx.mock:
        respx.get(INDEX).mock(side_effect=httpx.ConnectError("boom"))

        with pytest.raises(SourceDiscoveryError):
            [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert adapter.enumeration_complete is False


@pytest.mark.asyncio
async def test_discover_marks_enumeration_incomplete_on_a_non_200_index_response(
    make_source_config, search_profile, sample_keywords
) -> None:
    """Nothing was enumerated at all - this must be as loud as a zero-row
    parse, not silently swallowed into "no results this run".
    """
    adapter = get_adapter(
        make_source_config(key="denkmalboerse", adapter="denkmalboerse", base_url=BASE)
    )

    with respx.mock:
        respx.get(INDEX).mock(return_value=httpx.Response(503))

        with pytest.raises(SourceDiscoveryError):
            [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert adapter.enumeration_complete is False


@pytest.mark.asyncio
async def test_discover_marks_enumeration_incomplete_when_a_detail_fetch_fails(
    make_source_config, search_profile, sample_keywords, read_fixture
) -> None:
    """One bad detail page must not abort the run (fetch_detail already
    swallows it and returns None), but it does mean this run did not actually
    see everything the index promised - silence about the rest is no longer
    trustworthy.
    """
    adapter = get_adapter(
        make_source_config(key="denkmalboerse", adapter="denkmalboerse", base_url=BASE)
    )
    # Object 007505 ("Mühldorf am Inn") is Oberbayern and inside the default
    # 80km radius, so it would normally be fetched - here it 404s instead.
    failing_detail = f"{BASE}/information-service/denkmalboerse/objekte/007505/index.html"

    with respx.mock:
        respx.get(INDEX).mock(
            return_value=httpx.Response(200, text=read_fixture(SEARCH_FIXTURE_NAME))
        )
        respx.get(failing_detail).mock(return_value=httpx.Response(404))
        respx.route(url__regex=DETAIL_URL_RE.pattern).mock(
            return_value=httpx.Response(200, text="<html><body>Objekt</body></html>")
        )

        [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert adapter.enumeration_complete is False
