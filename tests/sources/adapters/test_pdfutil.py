"""``_pdfutil``: the PDF -> text lift shared by the Denkmalbörse fetch, the
paste box's upload and every Amtsblatt bulletin.

Coverage mirrors ``test_htmlutil.py``'s shape: the string-level extraction
first (``extract_pdf_text``, ``find_pdf_links``, ``pdf_title``), then the two
``RawListing`` builders that sit on top of it. The no-text-layer path matters
most - a scanned exposé must produce a warning the reader can see, never an
empty description that looks like a thin advert (see CLAUDE.md's "silence
that looks like success").
"""

from __future__ import annotations

import builtins
import sys
from pathlib import Path

import pytest

from hofradar.contracts import DocumentRef, RawListing
from hofradar.sources.adapters import _pdfutil
from hofradar.sources.adapters._pdfutil import (
    PDF_MAGIC,
    PdfText,
    PdfTooLarge,
    PdfUnavailable,
    PdfUnreadable,
    extract_pdf_text,
    find_pdf_links,
    is_pdf_response,
    looks_like_pdf,
    merge_pdf_into_listing,
    pdf_title,
    raw_listing_from_pdf,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fixtures"))
from pdf import make_pdf  # noqa: E402

# --------------------------------------------------------------------------- #
# extract_pdf_text
# --------------------------------------------------------------------------- #


def test_extract_pdf_text_returns_one_string_per_page_in_order():
    data = make_pdf([["Erste Seite"], ["Zweite Seite"], ["Dritte Seite"]])

    result = extract_pdf_text(data)

    assert result.page_count == 3
    assert "Erste Seite" in result.pages[0]
    assert "Zweite Seite" in result.pages[1]
    assert "Dritte Seite" in result.pages[2]


def test_extract_pdf_text_keeps_umlauts_and_euro_sign_intact():
    data = make_pdf([["Kaufpreis: 750.000 €", "Grundstücksfläche über München"]])

    result = extract_pdf_text(data)

    assert "€" in result.pages[0]
    assert "Grundstücksfläche" in result.pages[0]
    assert "München" in result.pages[0]


def test_text_property_joins_non_empty_pages_with_a_blank_line():
    data = make_pdf([["Seite eins"], [], ["Seite drei"]])

    result = extract_pdf_text(data)

    assert result.text == "Seite eins\n\nSeite drei"


def test_a_pdf_with_no_text_layer_reports_has_text_false_and_warns():
    data = make_pdf([[]])

    result = extract_pdf_text(data)

    assert result.has_text is False
    assert result.text == ""
    assert _pdfutil.WARNING_NO_TEXT_LAYER in result.warnings


def test_bytes_that_are_not_a_pdf_raise_pdf_unreadable():
    with pytest.raises(PdfUnreadable):
        extract_pdf_text(b"this is definitely not a PDF file")


def test_more_than_pdf_max_bytes_raises_pdf_too_large_without_allocating_it(monkeypatch):
    # A real 40 MB+ allocation would be wasteful and slow; shrinking the limit
    # to well below our tiny fixture's size exercises the same guard.
    monkeypatch.setattr(_pdfutil, "PDF_MAX_BYTES", 10)
    data = make_pdf([["Kaufpreis: 1 EUR, das ist lang genug"]])

    with pytest.raises(PdfTooLarge):
        extract_pdf_text(data)


def test_pypdf_missing_raises_pdf_unavailable(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("no pypdf installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    data = make_pdf([["Kaufpreis: 1 EUR"]])

    with pytest.raises(PdfUnavailable):
        extract_pdf_text(data)


# --------------------------------------------------------------------------- #
# looks_like_pdf / is_pdf_response
# --------------------------------------------------------------------------- #


def test_looks_like_pdf_tolerates_leading_whitespace():
    assert looks_like_pdf(b"   \n\t" + PDF_MAGIC + b"-1.4 ...")


def test_looks_like_pdf_false_for_other_content():
    assert looks_like_pdf(b"<html><body>not a pdf</body></html>") is False


def test_is_pdf_response_via_content_type_header():
    assert is_pdf_response("application/pdf; charset=binary", "https://x.test/y") is True


def test_is_pdf_response_via_pdf_path():
    assert is_pdf_response(None, "https://x.test/expose.PDF") is True


def test_is_pdf_response_via_magic_bytes_with_octet_stream():
    # A broker's download link is often served as application/octet-stream -
    # this is exactly what the paste box needs to still recognise it.
    body = PDF_MAGIC + b"-1.4 rest of a real pdf"
    assert is_pdf_response("application/octet-stream", "https://x.test/expose?id=123", body) is True


def test_is_pdf_response_false_for_html():
    body = b"<html><body>hello</body></html>"
    assert is_pdf_response("text/html; charset=utf-8", "https://x.test/detail", body) is False


# --------------------------------------------------------------------------- #
# find_pdf_links
# --------------------------------------------------------------------------- #


def test_find_pdf_links_resolves_relative_hrefs():
    html = '<html><body><a href="/docs/expose.pdf">Exposé</a></body></html>'

    links = find_pdf_links(html, "https://example.test/hof/123")

    assert links == [("https://example.test/docs/expose.pdf", "Exposé")]


def test_find_pdf_links_deduplicates_identical_urls():
    html = (
        "<html><body>"
        '<a href="/docs/expose.pdf">Herunterladen</a>'
        '<a href="/docs/expose.pdf">Nochmal hier</a>'
        "</body></html>"
    )

    links = find_pdf_links(html, "https://example.test")

    assert len(links) == 1


def test_find_pdf_links_ignores_non_pdf_links():
    html = (
        "<html><body>"
        '<a href="/docs/expose.pdf">Exposé</a>'
        '<a href="/docs/grundriss.png">Grundriss</a>'
        '<a href="/kontakt">Kontakt</a>'
        "</body></html>"
    )

    links = find_pdf_links(html, "https://example.test")

    assert links == [("https://example.test/docs/expose.pdf", "Exposé")]


def test_find_pdf_links_uses_the_href_when_the_anchor_has_no_text():
    html = '<html><body><a href="/docs/expose.pdf"></a></body></html>'

    links = find_pdf_links(html, "https://example.test")

    assert links == [("https://example.test/docs/expose.pdf", "https://example.test/docs/expose.pdf")]


# --------------------------------------------------------------------------- #
# pdf_title
# --------------------------------------------------------------------------- #


def test_pdf_title_skips_a_broker_reference_line():
    # "E&V ID: W-047ZVH" has enough letters to pass the letter minimum, but a
    # short label followed by a colon is a fact line, not a headline - the
    # real Engel & Völkers cover page opens with exactly this.
    data = make_pdf([["E&V ID: W-047ZVH", "Hofstelle mit Stadel und Nebengebäuden"]])

    result = extract_pdf_text(data)

    assert pdf_title(result) == "Hofstelle mit Stadel und Nebengebäuden"


def test_pdf_title_keeps_a_headline_with_a_colon_deep_in_its_text():
    data = make_pdf([["Ehemaliges Wasserschloss in Unterbaar: ein Refugium mit Geschichte"]])

    result = extract_pdf_text(data)

    assert pdf_title(result) == (
        "Ehemaliges Wasserschloss in Unterbaar: ein Refugium mit Geschichte"
    )


def test_pdf_title_skips_a_line_below_the_letter_minimum():
    # "ID: 047215" has only 2 letters (I, D) - below _MIN_TITLE_LETTERS - so
    # this one line is skipped and the next headline-shaped line is taken.
    data = make_pdf([["ID: 047215", "Hofstelle mit Stadel"]])

    result = extract_pdf_text(data)

    assert pdf_title(result) == "Hofstelle mit Stadel"


def test_pdf_title_returns_none_when_the_first_page_has_no_text():
    data = make_pdf([[]])

    result = extract_pdf_text(data)

    assert pdf_title(result) is None


def test_pdf_title_truncates_past_the_max_length():
    long_line = "Hofstelle " * 30  # well past _MAX_PDF_TITLE_LEN (200)
    data = make_pdf([[long_line]])

    result = extract_pdf_text(data)
    title = pdf_title(result)

    assert title is not None
    assert title.endswith("...")
    # rstrip() before appending "..." can shave a trailing space off the cut
    # point, so the length is at most (not exactly) the limit plus the ellipsis.
    assert len(title) <= _pdfutil._MAX_PDF_TITLE_LEN + len("...")
    assert title.startswith(long_line[:50])


# --------------------------------------------------------------------------- #
# raw_listing_from_pdf
# --------------------------------------------------------------------------- #


def test_raw_listing_from_pdf_reads_the_blfd_template_shape():
    # Two labelled facts set apart by a run of spaces on one line, plus a
    # bare room count on its own short line - the exact shape the BLfD
    # exposé template uses.
    data = make_pdf(
        [
            [
                "Wohnfläche: ca. 1.050 m²          Grundstücksfläche: ca. 7.112 m²",
                "28 Zimmer",
                "Ein wunderschönes Anwesen mit viel Charme.",
            ]
        ]
    )

    listing = raw_listing_from_pdf("test_source", "https://example.test/expose.pdf", data)

    assert listing.living_raw == "ca. 1.050 m²"
    assert listing.land_raw == "ca. 7.112 m²"
    assert listing.rooms_raw == "28"
    assert listing.page_kind == "listing"
    assert listing.description is not None
    assert "Ein wunderschönes Anwesen" in listing.description
    assert listing.documents == [
        DocumentRef(
            kind="expose", url="https://example.test/expose.pdf", title=None, page_count=1
        )
    ]


def test_raw_listing_from_pdf_kind_and_document_title_override_the_defaults():
    data = make_pdf([["Kaufpreis: 500.000 €"]])

    listing = raw_listing_from_pdf(
        "test_source",
        "https://example.test/upload-1.pdf",
        data,
        kind="upload",
        document_title="Vom Nutzer hochgeladen",
    )

    assert listing.documents == [
        DocumentRef(
            kind="upload",
            url="https://example.test/upload-1.pdf",
            title="Vom Nutzer hochgeladen",
            page_count=1,
        )
    ]


def test_raw_listing_from_pdf_with_no_text_layer_warns_and_has_no_description():
    data = make_pdf([[]])

    listing = raw_listing_from_pdf("test_source", "https://example.test/scan.pdf", data)

    assert listing.description is None
    assert _pdfutil.WARNING_NO_TEXT_LAYER in listing.warnings
    # The document is still remembered - a scanned exposé is evidence too,
    # even though nothing could be read out of it this run.
    assert listing.documents[0].url == "https://example.test/scan.pdf"


# --------------------------------------------------------------------------- #
# merge_pdf_into_listing
# --------------------------------------------------------------------------- #


def test_merge_pdf_into_listing_never_overwrites_an_existing_raw_field():
    listing = RawListing(
        source_key="test_source",
        url="https://example.test/1",
        living_raw="999 m²",  # already known from the HTML page
        description="Kurzinfo von der Seite.",
    )
    pdf_text = extract_pdf_text(
        make_pdf([["Wohnfläche: 180 m²", "Kaufpreis: 500.000 €"]])
    )

    merge_pdf_into_listing(listing, pdf_text, document_url="https://example.test/1.pdf")

    assert listing.living_raw == "999 m²"  # unchanged: the page keeps precedence
    assert listing.price_raw == "500.000 €"  # the hole is filled


def test_merge_pdf_into_listing_appends_description_after_a_blank_line():
    listing = RawListing(
        source_key="test_source", url="https://example.test/1", description="Seite: kurz."
    )
    pdf_text = extract_pdf_text(make_pdf([["PDF-Text folgt hier."]]))

    merge_pdf_into_listing(listing, pdf_text, document_url="https://example.test/1.pdf")

    assert listing.description == "Seite: kurz.\n\nPDF-Text folgt hier."


def test_merge_pdf_into_listing_becomes_the_pdf_text_when_listing_had_none():
    listing = RawListing(source_key="test_source", url="https://example.test/1", description=None)
    pdf_text = extract_pdf_text(make_pdf([["Nur der PDF-Text."]]))

    merge_pdf_into_listing(listing, pdf_text, document_url="https://example.test/1.pdf")

    assert listing.description == "Nur der PDF-Text."


def test_merge_pdf_into_listing_extends_warnings_and_appends_the_document_ref():
    listing = RawListing(
        source_key="test_source",
        url="https://example.test/1",
        description="Seite.",
        warnings=["PDF: das Exposé konnte nicht heruntergeladen werden"],
        documents=[DocumentRef(kind="upload", url="https://example.test/earlier.pdf")],
    )
    pdf_text = PdfText(pages=[""], warnings=["PDF: Seite 1 konnte nicht gelesen werden"])

    merge_pdf_into_listing(
        listing,
        pdf_text,
        document_url="https://example.test/1.pdf",
        document_title="Das Exposé",
        kind="expose",
    )

    assert listing.warnings == [
        "PDF: das Exposé konnte nicht heruntergeladen werden",
        "PDF: Seite 1 konnte nicht gelesen werden",
    ]
    assert listing.documents == [
        DocumentRef(kind="upload", url="https://example.test/earlier.pdf"),
        DocumentRef(
            kind="expose", url="https://example.test/1.pdf", title="Das Exposé", page_count=1
        ),
    ]
