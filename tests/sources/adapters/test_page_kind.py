"""A fetched page has to prove it is a listing before anything remembers it.

Issue #10: a portal's "Merkliste" bookmark widget became a Property with a
public_id, a geocode and a score, because nothing between the fetch and the
database ever asked what kind of page had been fetched, and the title chain
handed the portal's own chrome over as the headline.

The obvious gate - "no price, no area, no year, no location: drop it" - would
not have caught it. Fed through ``raw_listing_from_html``, the real OVB search
capture used below produces a full set of plausible facts (property type from
one result card, land area from a second, living area from a third, town from
a fourth). A fact-count gate passes exactly the page it has to reject, so what
is asserted here is the *shape* of the page instead.
"""

from __future__ import annotations

import pytest
from selectolax.parser import HTMLParser

from hofradar.contracts import PAGE_KIND_INDEX, PAGE_KIND_LISTING, PAGE_KIND_UTILITY
from hofradar.sources.adapters._htmlutil import (
    is_utility_url,
    listing_title,
    page_kind,
    raw_listing_from_html,
)

SEARCH_FIXTURE = "ovbimmo_search_rosenheim.html"
DETAIL_FIXTURE = "ovbimmo_detail.html"
SEARCH_URL = "https://ovbimmo.de/kaufen/rosenheim-kreis"
DETAIL_URL = (
    "https://ovbimmo.de/immobilien/zweifamilienhaus-grosskarolinenfeld-"
    "grosser-sonniger-garten-H3N33B"
)

#: The page from the issue, reduced to what makes it a utility page: a
#: portal function with a heading and nothing on offer. Deliberately minimal -
#: no real capture of a logged-out bookmark list exists in this repository.
MERKLISTE_HTML = """<!DOCTYPE html>
<html lang="de"><head><title>Merkliste - OVBimmo.de</title>
<meta property="og:title" content="Merkliste - OVBimmo.de"></head>
<body><h1>Merkliste</h1>
<p>Sie haben noch keine Objekte gemerkt.</p>
<p>Kaufpreis: k. A.</p></body></html>
"""

#: An index page that states nothing about itself in JSON-LD - the shape a
#: small broker's result list has. Twelve cards under one path prefix.
CARD_INDEX_HTML = """<!DOCTYPE html>
<html lang="de"><head><title>Unsere Objekte</title></head><body>
<h1>Unsere Objekte</h1>
{cards}
</body></html>
""".format(
    cards="\n".join(
        f'<a href="/immobilien/hofstelle-{n}">Hofstelle {n}</a>' for n in range(1, 13)
    )
)


def _tree(html: str) -> HTMLParser:
    return HTMLParser(html)


# --------------------------------------------------------------------------- #
# page_kind
# --------------------------------------------------------------------------- #


def test_the_real_search_capture_is_an_index(read_fixture) -> None:
    assert page_kind(_tree(read_fixture(SEARCH_FIXTURE)), SEARCH_URL) == PAGE_KIND_INDEX


def test_the_real_detail_capture_is_a_listing(read_fixture) -> None:
    assert page_kind(_tree(read_fixture(DETAIL_FIXTURE)), DETAIL_URL) == PAGE_KIND_LISTING


def test_a_bookmark_widget_is_a_utility_page() -> None:
    assert page_kind(_tree(MERKLISTE_HTML), "https://ovbimmo.de/merkliste") == PAGE_KIND_UTILITY


def test_a_pasted_utility_page_carries_no_url_and_is_still_refused() -> None:
    """The issue's own route: the paste box invents ``manual:<timestamp>`` as
    the URL when a human pastes markup without one, so the path signal is not
    available and the page has to give itself away by its own heading."""
    kind = page_kind(_tree(MERKLISTE_HTML), "manual:2026-09-04T10:00:00+00:00")

    assert kind == PAGE_KIND_UTILITY


def test_result_cards_alone_make_a_page_an_index() -> None:
    """No JSON-LD, no utility path - just many sibling links of one shape."""
    assert page_kind(_tree(CARD_INDEX_HTML), "https://makler.example/objekte") == PAGE_KIND_INDEX


def test_a_detail_page_with_a_few_related_links_is_not_an_index() -> None:
    html = (
        "<html><head><title>Resthof in Vogtareuth</title></head><body>"
        "<h1>Resthof in Vogtareuth</h1><p>Kaufpreis: 595.000 €</p>"
        + "".join(f'<a href="/immobilien/aehnlich-{n}">Ähnlich</a>' for n in range(1, 5))
        + "</body></html>"
    )
    assert page_kind(_tree(html), "https://makler.example/immobilien/resthof") == PAGE_KIND_LISTING


def test_a_listing_json_ld_type_settles_it() -> None:
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"RealEstateListing",'
        '"name":"Vierseithof bei Wasserburg"}</script></head>'
        "<body>" + "".join(f'<a href="/immobilien/x-{n}">x</a>' for n in range(1, 30)) + "</body>"
        "</html>"
    )
    assert page_kind(_tree(html), "https://makler.example/immobilien/x-1") == PAGE_KIND_LISTING


# --------------------------------------------------------------------------- #
# is_utility_url
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "url",
    [
        "https://portal.example/merkliste",
        "https://portal.example/de/suchagent/",
        "https://portal.example/login.php",
        "https://portal.example/impressum",
        "https://portal.example/datenschutz?x=1",
        "https://portal.example/suche",
        "https://portal.example/search",
    ],
)
def test_a_utility_path_is_recognised(url: str) -> None:
    assert is_utility_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        # Word-boundary aware: none of these IS a utility path, they only
        # start with the same letters or carry them inside a slug.
        "https://portal.example/suchergebnisse/rosenheim",
        "https://portal.example/immobilien/kontakthof-am-see-GZ12AB",
        "https://portal.example/immobilien/loginhof-bei-rosenheim",
        "https://ovbimmo.de/kaufen/rosenheim-kreis",
        "manual:2026-09-04T10:00:00+00:00",
    ],
)
def test_a_listing_path_is_not_mistaken_for_a_utility_one(url: str) -> None:
    assert is_utility_url(url) is False


# --------------------------------------------------------------------------- #
# listing_title
# --------------------------------------------------------------------------- #


def test_the_headline_wins_over_the_portals_own_page_title(read_fixture) -> None:
    """og:title on the search capture is the portal's chrome, ``<h1>`` is what
    the page is about. Neither is a listing - but the title that reaches a
    human must at least be the page's own headline."""
    title = listing_title(_tree(read_fixture(SEARCH_FIXTURE)), SEARCH_URL)

    assert title == "Immobilien in Rosenheim (Kreis) kaufen"


def test_a_json_ld_name_is_preferred_over_the_markup() -> None:
    html = (
        '<html><head><title>Hof kaufen | Makler Müller</title>'
        '<script type="application/ld+json">'
        '{"@type":"RealEstateListing","name":"Vierseithof bei Wasserburg"}'
        "</script></head><body><h1>Objektdetails</h1></body></html>"
    )
    assert listing_title(_tree(html), "https://mueller.example/objekt/1") == (
        "Vierseithof bei Wasserburg"
    )


def test_a_trailing_site_name_is_stripped() -> None:
    html = "<html><head><title>Resthof in Vogtareuth - ovbimmo.de</title></head></html>"
    assert listing_title(_tree(html), DETAIL_URL) == "Resthof in Vogtareuth"


def test_a_trailing_site_name_from_og_site_name_is_stripped() -> None:
    html = (
        "<html><head><title>Resthof in Vogtareuth | Makler Müller</title>"
        '<meta property="og:site_name" content="Makler Müller"></head></html>'
    )
    assert listing_title(_tree(html), "https://andere.example/objekt/1") == (
        "Resthof in Vogtareuth"
    )


def test_a_dash_that_is_not_a_site_name_survives() -> None:
    """The strip is conservative on purpose: everything after the last dash is
    routinely part of the headline, and losing it is a worse bug than keeping
    a site name."""
    html = "<html><head><title>Hofstelle mit Stadel - 8.000 m² Grund</title></head></html>"
    assert listing_title(_tree(html), DETAIL_URL) == "Hofstelle mit Stadel - 8.000 m² Grund"


def test_a_page_with_no_title_at_all_yields_none() -> None:
    assert listing_title(_tree("<html><body><p>nichts</p></body></html>"), DETAIL_URL) is None


# --------------------------------------------------------------------------- #
# raw_listing_from_html carries both
# --------------------------------------------------------------------------- #


def test_raw_listing_from_html_records_the_page_kind(read_fixture) -> None:
    search = raw_listing_from_html("ovbimmo", SEARCH_URL, read_fixture(SEARCH_FIXTURE))
    detail = raw_listing_from_html("ovbimmo", DETAIL_URL, read_fixture(DETAIL_FIXTURE))

    assert search.page_kind == PAGE_KIND_INDEX
    assert search.title == "Immobilien in Rosenheim (Kreis) kaufen"
    assert detail.page_kind == PAGE_KIND_LISTING
    assert detail.title == "Zweifamilienhaus! Großkarolinenfeld! Großer sonniger Garten!"


def test_a_plain_detail_page_defaults_to_listing() -> None:
    listing = raw_listing_from_html(
        "manual", "https://makler.example/hof-1", "<html><body><h1>Hof</h1></body></html>"
    )
    assert listing.page_kind == PAGE_KIND_LISTING
