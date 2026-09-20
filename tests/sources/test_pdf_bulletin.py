"""PdfBulletinAdapter: walking an Amtsblatt index page for keyword hits.

The adapter never enumerates (``enumerates = False`` - a bulletin archive
accumulates and never retracts), so these tests only cover discovery, not
absence. The shared PDF reading now lives in ``_pdfutil``; this file exercises
the adapter's own logic - link discovery on the index, per-page keyword
scanning, and refusing to let one bad PDF or index page abort the whole run.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
import respx

from hofradar.sources import get_adapter
from hofradar.sources.exceptions import SourceDiscoveryError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "fixtures"))
from pdf import make_pdf  # noqa: E402


@pytest.mark.asyncio
async def test_discover_yields_one_hit_per_page_and_term(
    make_source_config, search_profile, sample_keywords
):
    # Page 1 carries exactly one vocabulary term ("Stadel"); page 2 carries
    # none, so it must not appear in the results at all.
    pdf_bytes = make_pdf([["Alter Hof mit Stadel zu verkaufen."], ["Nichts von Belang hier."]])
    index_html = (
        '<html><body><a href="/blatt/kw34.pdf">Gemeindeblatt KW 34, 01.09.2026</a></body></html>'
    )
    cfg = make_source_config(
        key="gemeindeblatt_pdf",
        adapter="pdf_bulletin",
        base_url="https://gemeinde.example",
        options={"bulletins": ["https://gemeinde.example/amtsblatt"]},
    )
    adapter = get_adapter(cfg)

    with respx.mock:
        respx.get("https://gemeinde.example/amtsblatt").mock(
            return_value=httpx.Response(200, text=index_html)
        )
        respx.get("https://gemeinde.example/blatt/kw34.pdf").mock(
            return_value=httpx.Response(
                200, content=pdf_bytes, headers={"Content-Type": "application/pdf"}
            )
        )
        results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert len(results) == 1
    hit = results[0]
    assert hit.url == "https://gemeinde.example/blatt/kw34.pdf#page=1"
    assert hit.extra["page_number"] == 1
    assert hit.extra["matched_term"] == "Stadel"
    assert hit.extra["document_url"] == "https://gemeinde.example/blatt/kw34.pdf"
    assert hit.extra["document_date"] == "01.09.2026"
    assert hit.extra["issue"] == "KW 34"


@pytest.mark.asyncio
async def test_a_pdf_response_that_is_not_a_pdf_is_skipped_and_the_run_continues(
    make_source_config, search_profile, sample_keywords
):
    good_pdf = make_pdf([["Bauernhof mit Scheune."]])
    index_html = (
        "<html><body>"
        '<a href="/blatt/broken.pdf">Kaputtes Blatt</a>'
        '<a href="/blatt/good.pdf">Gutes Blatt</a>'
        "</body></html>"
    )
    cfg = make_source_config(
        key="gemeindeblatt_pdf",
        adapter="pdf_bulletin",
        base_url="https://gemeinde.example",
        options={"bulletins": ["https://gemeinde.example/amtsblatt"]},
    )
    adapter = get_adapter(cfg)

    with respx.mock:
        respx.get("https://gemeinde.example/amtsblatt").mock(
            return_value=httpx.Response(200, text=index_html)
        )
        respx.get("https://gemeinde.example/blatt/broken.pdf").mock(
            return_value=httpx.Response(200, text="<html>gar kein PDF</html>")
        )
        respx.get("https://gemeinde.example/blatt/good.pdf").mock(
            return_value=httpx.Response(
                200, content=good_pdf, headers={"Content-Type": "application/pdf"}
            )
        )
        results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert {hit.extra["document_url"] for hit in results} == {
        "https://gemeinde.example/blatt/good.pdf"
    }


@pytest.mark.asyncio
async def test_source_discovery_error_when_no_index_yields_a_pdf_link(
    make_source_config, search_profile, sample_keywords
):
    index_html = "<html><body><a href='/impressum'>Impressum</a></body></html>"
    cfg = make_source_config(
        key="gemeindeblatt_pdf",
        adapter="pdf_bulletin",
        base_url="https://gemeinde.example",
        options={"bulletins": ["https://gemeinde.example/amtsblatt"]},
    )
    adapter = get_adapter(cfg)

    with respx.mock:
        respx.get("https://gemeinde.example/amtsblatt").mock(
            return_value=httpx.Response(200, text=index_html)
        )
        with pytest.raises(SourceDiscoveryError):
            async for _ in adapter.discover(search_profile, sample_keywords):
                pass
