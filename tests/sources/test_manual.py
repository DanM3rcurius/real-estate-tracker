"""ManualAdapter: paste-ingest from plain text and from fetched/pasted HTML."""

from __future__ import annotations

import httpx
import pytest
import respx

from hofradar.sources.adapters.manual import (
    BLOCKED_PAGE_NOTICE,
    SCRIPT_PAGE_NOTICE,
    ManualAdapter,
)
from hofradar.sources.exceptions import PageUnreadable
from tests.fixtures.pdf import make_pdf

#: The lines a broker's exposé PDF actually carries: a headline, then the
#: same labelled fields an HTML detail page would have put in a table.
PDF_EXPOSE_LINES = [
    [
        "Hofstelle mit Stadel bei Vogtareuth",
        "Kaufpreis: 595.000 EUR",
        "Wohnflaeche: 240 m2",
        "Grundstueck: 8.000 m2",
        "Baujahr: 1891",
    ],
    ["Scheune, Stall und Tenne. Obstgarten. Teilung moeglich."],
]

PLAIN_EXPOSE = """\
Gepflegte Hofstelle mit Scheune in Alleinlage
Kaufpreis: 480.000 €
Wohnfläche: 195 m²
Grundstück: 4.200 m²
Baujahr: 1888
Ort: Feldkirchen-Westerham

Diese ehemalige Landwirtschaft bietet Stadel, Stall und viel
Entwicklungspotenzial. Kein Makler, provisionsfrei.

https://img.example.host/foto1.jpg
https://img.example.host/foto2.png
"""


@pytest.fixture
def adapter(make_source_config):
    cfg = make_source_config(key="manual", adapter="manual", role="primary")
    return ManualAdapter(cfg)


def test_ingest_text_from_plain_paste(adapter):
    listing = adapter.ingest_text("https://user-pasted.example/no-real-url", PLAIN_EXPOSE)

    assert listing.source_key == "manual"
    assert listing.title == "Gepflegte Hofstelle mit Scheune in Alleinlage"
    assert listing.price_raw == "480.000 €"
    assert listing.living_raw == "195 m²"
    assert listing.land_raw == "4.200 m²"
    assert listing.year_raw == "1888"
    assert listing.location_raw == "Feldkirchen-Westerham"
    assert "Entwicklungspotenzial" in listing.description
    assert listing.image_urls == [
        "https://img.example.host/foto1.jpg",
        "https://img.example.host/foto2.png",
    ]


def test_ingest_text_from_html(adapter, read_fixture):
    html = read_fixture("detail_live.html")
    listing = adapter.ingest_text("https://makler.example/hof-1", html)

    assert listing.title == "Hofstelle mit Scheune bei Feldkirchen-Westerham"
    assert listing.price_raw == "590.000 €"
    assert listing.living_raw == "210 m²"
    assert listing.land_raw == "6.500 m²"
    assert "Nebengebäuden" in listing.description or "Entwicklungspotenzial" in listing.description
    assert listing.image_urls == [
        "https://makler.example/bilder/titel.jpg",
        "https://makler.example/bilder/hof-1.jpg",
        "https://makler.example/bilder/hof-2.jpg",
    ]


@pytest.mark.asyncio
async def test_ingest_url_fetches_and_parses(adapter, read_fixture):
    html = read_fixture("detail_live.html")
    with respx.mock:
        respx.get("https://makler.example/hof-2").mock(return_value=httpx.Response(200, text=html))
        listing = await adapter.ingest_url("https://makler.example/hof-2")

    assert listing is not None
    assert listing.http_status == 200
    assert listing.listing_visible is True
    assert listing.title == "Hofstelle mit Scheune bei Feldkirchen-Westerham"


@pytest.mark.asyncio
async def test_ingest_url_detects_gone_listing(adapter, read_fixture):
    html = read_fixture("detail_gone.html")
    with respx.mock:
        respx.get("https://makler.example/hof-3").mock(return_value=httpx.Response(200, text=html))
        listing = await adapter.ingest_url("https://makler.example/hof-3")

    assert listing is not None
    assert listing.listing_visible is False


@pytest.mark.asyncio
async def test_ingest_url_handles_fetch_failure_gracefully(adapter):
    with respx.mock:
        respx.get("https://makler.example/unreachable").mock(side_effect=httpx.ConnectError("boom"))
        listing = await adapter.ingest_url("https://makler.example/unreachable")

    assert listing is None


@pytest.mark.asyncio
async def test_discover_yields_nothing(adapter, search_profile, sample_keywords):
    results = [item async for item in adapter.discover(search_profile, sample_keywords)]
    assert results == []


@pytest.mark.asyncio
async def test_ingest_url_reads_a_pdf_behind_the_link(adapter):
    """A URL that answers with a PDF is a listing, not an unreadable page."""
    pdf = make_pdf(PDF_EXPOSE_LINES)
    with respx.mock:
        respx.get("https://makler.example/expose-1.pdf").mock(
            return_value=httpx.Response(
                200, content=pdf, headers={"content-type": "application/pdf"}
            )
        )
        listing = await adapter.ingest_url("https://makler.example/expose-1.pdf")

    assert listing is not None
    assert listing.http_status == 200
    assert listing.listing_visible is True
    assert listing.title == "Hofstelle mit Stadel bei Vogtareuth"
    assert listing.price_raw == "595.000 EUR"
    assert listing.living_raw == "240 m2"
    assert listing.land_raw == "8.000 m2"
    assert listing.year_raw == "1891"
    assert "Obstgarten" in listing.description
    assert [doc.url for doc in listing.documents] == ["https://makler.example/expose-1.pdf"]


@pytest.mark.asyncio
async def test_ingest_url_reads_a_pdf_served_as_octet_stream(adapter):
    """Download links answer ``application/octet-stream`` more often than not."""
    pdf = make_pdf(PDF_EXPOSE_LINES)
    with respx.mock:
        respx.get("https://makler.example/download/expose.pdf").mock(
            return_value=httpx.Response(
                200, content=pdf, headers={"content-type": "application/octet-stream"}
            )
        )
        listing = await adapter.ingest_url("https://makler.example/download/expose.pdf")

    assert listing is not None
    assert listing.price_raw == "595.000 EUR"
    assert listing.listing_visible is True
    assert [doc.url for doc in listing.documents] == [
        "https://makler.example/download/expose.pdf"
    ]


def test_ingest_pdf_marks_the_document_as_an_upload(adapter):
    listing = adapter.ingest_pdf(
        "upload:0123456789abcdef", make_pdf(PDF_EXPOSE_LINES), filename="expose.pdf"
    )

    assert listing.url == "upload:0123456789abcdef"
    assert listing.price_raw == "595.000 EUR"
    assert len(listing.documents) == 1
    document = listing.documents[0]
    assert document.kind == "upload"
    assert document.title == "expose.pdf"
    assert document.page_count == 2


def test_ingest_pdf_warns_about_a_scan_instead_of_returning_nothing(adapter):
    listing = adapter.ingest_pdf("upload:deadbeef", make_pdf([[]]), filename="scan.pdf")

    assert listing.description is None
    assert any("gescannt" in warning for warning in listing.warnings)


def test_ingest_text_recovers_unmapped_ligatures_copied_out_of_a_pdf(adapter):
    # What a PDF viewer's copy - and the repair script's re-read of a stored
    # upload - hands over when the font left its ligatures unmapped.
    listing = adapter.ingest_text(
        "upload:abc", "Haus am See\nWohnŦäche ca. 140 m²\nDie VerpŦichtung entfällt."
    )

    assert listing.living_raw == "140 m²"
    assert "Verpflichtung" in listing.description
    assert any("Ligatur" in warning for warning in listing.warnings)


#: What ImmoScout answered a pasted exposé link with (HTTP 401), abridged.
BOT_WALL = """<html><head><title>Ich bin kein Roboter - ImmobilienScout24</title></head>
<body><h1>Ich bin kein Roboter</h1>
<p>Du bist ein Mensch aus Fleisch und Blut? Entschuldige bitte, dann hat unser System
dich fälschlicherweise als Roboter identifiziert.</p></body></html>"""

#: A CloudFront "Interaktives Exposé": a title and a script, nothing to read.
SCRIPT_SHELL = """<html><head><title>Interaktives Exposé</title></head>
<body><div id="root"></div><script src="/static/js/main.js"></script></body></html>"""


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 200])
async def test_ingest_url_refuses_a_bot_wall(adapter, status):
    """Stored, it became a property titled "Ich bin kein Roboter" with every
    fact "k. A.". Refused, with the reason in the reader's words - and never
    retried or worked around."""
    url = "https://www.immobilienscout24.de/expose/169936773"
    with respx.mock:
        route = respx.get(url).mock(return_value=httpx.Response(status, text=BOT_WALL))
        with pytest.raises(PageUnreadable) as caught:
            await adapter.ingest_url(url)

    assert caught.value.notice == BLOCKED_PAGE_NOTICE
    assert route.call_count == 1


@pytest.mark.asyncio
async def test_ingest_url_refuses_a_page_its_javascript_would_have_filled(adapter):
    url = "https://d22fpj9jctru7v.cloudfront.net/?exposeId=44fc6015"
    with respx.mock:
        respx.get(url).mock(return_value=httpx.Response(200, text=SCRIPT_SHELL))
        with pytest.raises(PageUnreadable) as caught:
            await adapter.ingest_url(url)

    assert caught.value.notice == SCRIPT_PAGE_NOTICE


#: A whole OVBimmo detail page selected and copied as text: the site's
#: navigation first, the headline further down. Thirteen pastes stored this
#: way were titled "Merkliste".
COPIED_PORTAL_PAGE = """Merkliste
0
Benutzermenü
Homepage
Gesuche
Login
Rosenheim (Kreis)
Bernau a. Chiemsee
Exposé
REH in Bernau mit DHH-Charakter und kleinem Garten - sofort frei!
Kaufpreis
699.000,-€
"""

_OVB_URL = (
    "https://ovbimmo.de/immobilien/"
    "reh-in-bernau-mit-dhh-charakter-und-kleinem-garten-sofort-frei-H3B2JB?t=all:sale:living"
)


def test_a_copied_portal_page_is_titled_by_the_line_its_url_names(adapter):
    listing = adapter.ingest_text(_OVB_URL, COPIED_PORTAL_PAGE)
    assert listing.title == "REH in Bernau mit DHH-Charakter und kleinem Garten - sofort frei!"


def test_the_slug_match_reads_umlauts_the_way_urls_spell_them(adapter):
    text = "Merkliste\nSeenähe und Bergblick - gepflegtes Einfamilienhaus\nKaufpreis: 1 €"
    url = "https://ovbimmo.de/immobilien/seenaehe-und-bergblick-gepflegtes-einfamilienhaus-H52Q5Z"
    listing = adapter.ingest_text(url, text)
    assert listing.title == "Seenähe und Bergblick - gepflegtes Einfamilienhaus"


def test_without_a_slug_portal_chrome_is_still_never_the_title(adapter):
    listing = adapter.ingest_text("manual:2026-09-27T20:00:00+00:00", COPIED_PORTAL_PAGE)
    assert listing.title not in {"Merkliste", "0", "Benutzermenü", "Homepage", "Login"}


def test_a_short_town_name_is_not_taken_for_the_slug(adapter):
    text = "Bernau\nGepflegte Hofstelle mit Scheune\nKaufpreis: 480.000 €"
    url = "https://makler.example/objekt/bernau-hofstelle-mit-scheune-und-stadel-4711"
    assert adapter.ingest_text(url, text).title == "Bernau"
