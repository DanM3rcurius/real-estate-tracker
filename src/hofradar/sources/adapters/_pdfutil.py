"""Shared PDF -> text lift, the sibling of ``_htmlutil`` for the other page format.

Why this exists: on the Denkmalbörse the detail page is a short "Kurzinfo"
box and a link, and the exposé itself is a PDF behind that link. The same is
true of most broker exposés and of every Amtsblatt. Reading the HTML and
ignoring the PDF is how a listing ends up with ``rooms``, ``usable_sqm`` and
half its description reading "k. A." while the fact sat one click away. So
a PDF is treated as what it is - the listing's own words in a different
container - and lifted at the same string level as an HTML page: text out,
labelled lines picked up, typed parsing left to ``hofradar.normalize``.

Two things are refused loudly rather than silently. A PDF with no text
layer (a scanned exposé) yields nothing, and that is reported as a warning
on the listing, not as an empty description that looks like a thin advert.
And a file over :data:`PDF_MAX_BYTES` is not read at all: a crawl that pulls
a 200 MB brochure into memory per object is not polite, and an upload of
that size is not an exposé.

``pypdf`` is imported lazily so that importing this module costs nothing
and a missing package surfaces as :class:`PdfUnavailable`, which every
caller turns into a warning the reader can see (invariant: silence that
looks like success is the bug this codebase keeps having).
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser

from hofradar.contracts import DocumentRef, RawListing
from hofradar.sources.adapters._htmlutil import extract_labeled_fields

logger = logging.getLogger(__name__)

#: The largest exposé seen on the Denkmalbörse in September 2026 was 13.6 MB
#: (17 pages of photographs). Anything past this is a brochure, not an
#: advert, and is refused before a byte of it is parsed.
PDF_MAX_BYTES = 40 * 1024 * 1024

#: Every PDF starts with this signature; a response that claims to be one
#: and does not is an error page in disguise.
PDF_MAGIC = b"%PDF"

PDF_CONTENT_TYPE = "application/pdf"

#: What a source-level document reference is called when it is the
#: listing's own exposé (as opposed to an Amtsblatt hit or a reader's upload).
DOCUMENT_KIND_EXPOSE = "expose"
DOCUMENT_KIND_UPLOAD = "upload"

#: How a PDF page is separated from the next in the joined text, so a
#: "Label: value" that ends one page never runs into the first line of the
#: next.
_PAGE_SEPARATOR = "\n\n"

#: A title has to be one line of prose, not the first 2 KB of a cover page.
_MAX_PDF_TITLE_LEN = 200

#: A cover page's first line is often a broker's reference ("E&V ID:
#: W-047ZVH"), a page label or a single word. A title candidate has to look
#: like a headline: at least this many letters.
_MIN_TITLE_LETTERS = 6

_LETTERS_RE = re.compile(r"[^\W\d_]", re.UNICODE)

#: A "Label: value" line is a fact, not a headline - "E&V ID: W-047ZVH" opens
#: a broker's cover page and "Kaufpreis: 59.000 €" its fact box. A colon
#: after a short label is what marks it; a headline that merely contains a
#: colon deep in its text ("Wasserschloss: ein Refugium") is left alone.
_LABEL_LINE_RE = re.compile(r"^[^:]{1,30}:(?:\s|$)")

#: The line under a bare "Ihr Gesprächspartner:" is a broker's name, which
#: has as many letters as a headline and no colon to give it away.
_CONTACT_LABEL_RE = re.compile(
    r"(?:ansprech|gesprächs)partner|kontakt|makler|berater|anbieter", re.IGNORECASE
)
#: An e-mail address or a web address is never a headline.
_ADDRESS_LINE_RE = re.compile(r"@|https?://|\bwww\.", re.IGNORECASE)

#: A cover can open with its fact box and no colon in it: ohne-makler.net's
#: text layer starts "Baujahr 1993", "Grundstücksfläche 670 m²", ... and sets
#: the headline after the box (issue #31). Most of its rows name a label the
#: fact reader does not know and carry no figure ("Energieträger Gas",
#: "Zustand gepflegt"), so the box is recognised as a block, not row by row: a
#: line the fact reader reads a fact off opens it, and a row of a label and a
#: short value - at most this many words - continues it.
_MAX_FACT_ROW_WORDS = 3

#: Unicode's own ligature code points (U+FB00-U+FB06). They are honest text,
#: but a label reader that knows "wohnfläche" never matches "Wohnﬂäche", so
#: they are spelled out before anything reads them.
_PRESENTATION_LIGATURES = str.maketrans(
    {
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\ufb05": "st",
        "\ufb06": "st",
    }
)

#: The f-ligatures a font draws as one glyph.
_F_LIGATURES = ("ffi", "ffl", "ff", "fi", "fl")

#: Exposé words spelled with an f-ligature, cut to the stem around it. When a
#: font's ToUnicode map leaves a ligature glyph out, pypdf emits the glyph's
#: number as a character ("WohnŦäche": glyph 0x166 is fl), and nothing in the
#: file says which ligature it was - so each such character is read as the
#: ligature that turns the most of its words into one of these. A stem only
#: counts when it spans the whole replaced ligature, or "beffindet" would
#: score for ffi on the strength of "find".
_LIGATURE_STEMS = (
    # fl
    "fläch", "flach", "pflicht", "pfleg", "pflanz", "pflast", "flur", "fliese", "flügel",
    "fluss", "flieg", "flug", "flex", "aufl", "einfl",
    # fi
    "find", "profit", "finanz", "firm", "fisch", "fix", "grafi", "defini",
    # ffi
    "effizien", "offizi",
    # ff
    "offen", "öffn", "öffentl", "stoff", "griff", "treff", "schaff", "hoff", "pfeff",
    # ffl
    "trefflich", "stoffl",
)

#: A character above Latin-1 inside a word is the only kind that can be a
#: stray glyph number: everything German, and the typography around it
#: (€, „“, –), is either Latin-1 or not a letter.
_LATIN_1_MAX = 0xFF
_WORD_RE = re.compile(r"\w+")

#: Warnings are German because they reach the reader on /add and in the
#: observation's raw record - same rule as ``hofradar.normalize``.
WARNING_NO_TEXT_LAYER = (
    "PDF: das Dokument enthält keinen lesbaren Text (vermutlich gescannt) - "
    "es wurde nichts daraus gelesen"
)
WARNING_PDF_UNAVAILABLE = (
    "PDF: die PDF-Verarbeitung ist nicht installiert (pip install 'hofradar[pdf]') - "
    "das Exposé wurde nicht gelesen"
)


class PdfError(ValueError):
    """Base for everything this module refuses to read."""


class PdfUnavailable(PdfError):
    """``pypdf`` is not installed."""


class PdfTooLarge(PdfError):
    """The file exceeds :data:`PDF_MAX_BYTES`."""


class PdfUnreadable(PdfError):
    """Not a PDF, or one ``pypdf`` cannot open."""


@dataclass(slots=True)
class PdfText:
    """What came out of one PDF: one string per page, plus what did not."""

    pages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def has_text(self) -> bool:
        return any(page.strip() for page in self.pages)

    @property
    def text(self) -> str:
        return _PAGE_SEPARATOR.join(page.strip() for page in self.pages if page.strip())


def _lazy_pypdf() -> Any:
    try:
        import pypdf
    except ImportError as exc:  # pragma: no cover - exercised only without the extra
        raise PdfUnavailable("pypdf is not installed: pip install 'hofradar[pdf]'") from exc
    return pypdf


def looks_like_pdf(data: bytes) -> bool:
    """Is this byte string a PDF file? Leading whitespace/BOM tolerated."""
    return data.lstrip()[: len(PDF_MAGIC)] == PDF_MAGIC


def is_pdf_url(url: str) -> bool:
    return urlsplit(url).path.lower().endswith(".pdf")


def is_pdf_response(content_type: str | None, url: str, body: bytes | None = None) -> bool:
    """Does this response carry a PDF? Header first, then the URL, then the bytes.

    The body check matters for the paste box: a broker's download link is
    often ``/expose?id=123`` served as ``application/octet-stream``.
    """
    if content_type and content_type.split(";", 1)[0].strip().lower() == PDF_CONTENT_TYPE:
        return True
    if is_pdf_url(url):
        return True
    return bool(body) and looks_like_pdf(body or b"")


def find_pdf_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Return (absolute_pdf_url, link_text) for every PDF link on a page."""
    tree = HTMLParser(html)
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in tree.css("a[href]"):
        href = anchor.attributes.get("href") or ""
        if not is_pdf_url(href):
            continue
        absolute = urljoin(base_url, href)
        if absolute in seen:
            continue
        seen.add(absolute)
        text = anchor.text(strip=True) or absolute
        links.append((absolute, text))
    return links


def extract_pdf_text(data: bytes) -> PdfText:
    """Every page's text layer, in order. Raises on what cannot be read at all.

    A page that fails to extract is skipped with a warning rather than
    failing the document: one broken content stream in a 17-page exposé
    must not lose the other sixteen.
    """
    if len(data) > PDF_MAX_BYTES:
        raise PdfTooLarge(f"PDF is {len(data)} bytes, limit is {PDF_MAX_BYTES}")
    if not looks_like_pdf(data):
        raise PdfUnreadable("not a PDF (missing %PDF signature)")
    pypdf = _lazy_pypdf()
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        page_iter = list(reader.pages)
    except Exception as exc:  # noqa: BLE001 - pypdf raises a zoo of its own types
        raise PdfUnreadable(f"could not open PDF: {exc}") from exc

    result = PdfText()
    for page_number, page in enumerate(page_iter, start=1):
        try:
            text = page.extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - one bad page must not lose the document
            logger.warning("could not extract text from PDF page %d: %s", page_number, exc)
            result.warnings.append(f"PDF: Seite {page_number} konnte nicht gelesen werden")
            text = ""
        result.pages.append(text)
    if not result.has_text:
        result.warnings.append(WARNING_NO_TEXT_LAYER)
    # Decided over the whole document, not page by page: the same glyph is the
    # same ligature on every page, and the cover alone may hold no word that
    # gives it away.
    pages = [page.translate(_PRESENTATION_LIGATURES) for page in result.pages]
    ligatures = _unmapped_ligatures("\n".join(pages))
    result.pages = [_spell_out(page, ligatures) for page in pages]
    if ligatures:
        result.warnings.append(_ligature_warning(ligatures))
    return result


def recover_ligatures(text: str) -> tuple[str, list[str]]:
    """Spell out the f-ligatures in text lifted from a PDF.

    Returns the text and, when a glyph had to be guessed, one warning saying
    which character was read as which ligature. For text that left a PDF some
    other way - a viewer's copy pasted into /add, an upload's stored text
    re-read by ``scripts/repair_pastes.py``; ``extract_pdf_text`` does the same
    for the file itself.
    """
    text = text.translate(_PRESENTATION_LIGATURES)
    ligatures = _unmapped_ligatures(text)
    if not ligatures:
        return text, []
    return _spell_out(text, ligatures), [_ligature_warning(ligatures)]


def _unmapped_ligatures(text: str) -> dict[str, str]:
    """Which stray characters in ``text`` stand for which f-ligature."""
    words_by_glyph: dict[str, set[str]] = {}
    for word in _WORD_RE.findall(text):
        for char in word:
            if ord(char) > _LATIN_1_MAX and char.isalpha():
                words_by_glyph.setdefault(char, set()).add(word)

    found: dict[str, str] = {}
    for glyph, words in words_by_glyph.items():
        scores = {
            ligature: sum(_stem_spans_ligature(word, glyph, ligature) for word in words)
            for ligature in _F_LIGATURES
        }
        best = max(scores.values())
        winners = [ligature for ligature, score in scores.items() if score == best]
        # Nothing matched, or two readings matched equally well: a guess the
        # words do not support is not made - the character stays as it is.
        if best and len(winners) == 1:
            found[glyph] = winners[0]
    return found


def _stem_spans_ligature(word: str, glyph: str, ligature: str) -> bool:
    spans: list[tuple[int, int]] = []
    spelled = ""
    for char in word:
        if char == glyph:
            spans.append((len(spelled), len(spelled) + len(ligature)))
            spelled += ligature
        else:
            spelled += char
    spelled = spelled.lower()
    for stem in _LIGATURE_STEMS:
        start = spelled.find(stem)
        while start != -1:
            end = start + len(stem)
            if any(start <= low and high <= end for low, high in spans):
                return True
            start = spelled.find(stem, start + 1)
    return False


def _spell_out(text: str, ligatures: dict[str, str]) -> str:
    for glyph, ligature in ligatures.items():
        text = text.replace(glyph, ligature)
    return text


def _ligature_warning(ligatures: dict[str, str]) -> str:
    readings = [f"„{glyph}“ als „{ligature}“" for glyph, ligature in sorted(ligatures.items())]
    listed = readings[0] if len(readings) == 1 else f"{', '.join(readings[:-1])} und {readings[-1]}"
    return (
        "PDF: die Schrift des Dokuments ordnet einigen Ligaturen keinen Text zu - "
        f"{listed} gelesen"
    )


def pdf_title(text: PdfText) -> str | None:
    """The first line on the first page that reads like a headline."""
    for page in text.pages:
        under_contact_label = False
        in_fact_table = False
        for line in page.splitlines():
            candidate = line.strip()
            if not candidate:
                continue
            follows_contact_label = under_contact_label
            under_contact_label = candidate.endswith(":") and bool(
                _CONTACT_LABEL_RE.search(candidate)
            )
            if follows_contact_label or _ADDRESS_LINE_RE.search(candidate):
                continue
            if len(_LETTERS_RE.findall(candidate)) < _MIN_TITLE_LETTERS:
                continue
            if _LABEL_LINE_RE.match(candidate):
                continue
            if extract_labeled_fields(candidate):
                in_fact_table = True
                continue
            if in_fact_table and len(candidate.split()) <= _MAX_FACT_ROW_WORDS:
                continue
            if len(candidate) > _MAX_PDF_TITLE_LEN:
                candidate = candidate[:_MAX_PDF_TITLE_LEN].rstrip() + "..."
            return candidate
        break
    return None


def _document_ref(url: str, text: PdfText, *, kind: str, title: str | None) -> DocumentRef:
    return DocumentRef(kind=kind, url=url, title=title, page_count=text.page_count)


def raw_listing_from_pdf(
    source_key: str,
    url: str,
    data: bytes,
    *,
    http_status: int | None = None,
    extra: dict[str, Any] | None = None,
    kind: str = DOCUMENT_KIND_EXPOSE,
    document_title: str | None = None,
) -> RawListing:
    """Turn one PDF into a RawListing - the whole document is the listing.

    Used when the PDF *is* what the reader handed over (an upload, a pasted
    link straight to an exposé). Raises :class:`PdfError` when nothing can be
    read; a readable PDF with no text layer returns a listing whose
    ``warnings`` say so, because the reader must see that rather than a
    property with an empty description.
    """
    text = extract_pdf_text(data)
    body = text.text
    labeled = extract_labeled_fields(body)
    return RawListing(
        source_key=source_key,
        url=url,
        title=pdf_title(text),
        description=body or None,
        http_status=http_status,
        fetched_at=datetime.now(UTC),
        extra=extra or {},
        warnings=list(text.warnings),
        documents=[_document_ref(url, text, kind=kind, title=document_title)],
        **labeled,
    )


def merge_pdf_into_listing(
    listing: RawListing,
    text: PdfText,
    *,
    document_url: str,
    document_title: str | None = None,
    kind: str = DOCUMENT_KIND_EXPOSE,
) -> None:
    """Enrich a page-derived listing with the exposé PDF behind its link.

    The page keeps precedence: a labelled field the page already filled is
    never overwritten, only holes are filled. The PDF's text is appended to
    the description so keyword extraction and the dossier see the whole
    exposé, and the document is recorded so the dossier can link to it.
    """
    labeled = extract_labeled_fields(text.text)
    for field_name, value in labeled.items():
        if not getattr(listing, field_name, None):
            setattr(listing, field_name, value)
    body = text.text
    if body:
        listing.description = (
            f"{listing.description}{_PAGE_SEPARATOR}{body}" if listing.description else body
        )
    listing.warnings.extend(text.warnings)
    listing.documents.append(
        _document_ref(document_url, text, kind=kind, title=document_title)
    )
