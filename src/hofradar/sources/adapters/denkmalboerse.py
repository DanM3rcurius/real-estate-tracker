"""The Bayerisches Landesamt für Denkmalpflege's Denkmalbörse.

Why this source is worth having: a large share of Bavarian farmsteads are
Baudenkmäler, owners list here free of charge, and nobody browses a state
authority's search CGI - so the competition for anything found here is a
fraction of a portal's. It is also the origin of the advert rather than a copy
of one, which is what earns it the ``primary`` role: the exposé is withdrawn
when the owner withdraws it, so its silence is the seller's own signal.

Structurally it is a static file tree with a Perl CGI search in front. Detail
pages are plain HTML at a predictable path keyed by a stable six-digit id, so
``fetch_detail`` and ``verify`` are ordinary cheap GETs and the id is a
first-class external identifier for deduplication.

The detail page itself is thin: a "Kurzinfo" box and a link to the exposé
PDF. Reading only the HTML is what made so many Denkmal properties render
"k. A." - on a live 30-object in-scope sample the HTML alone left rooms empty
on 30 of 30, usable area on 24 and living area on 9, while 29 of the 30 had
an exposé carrying exactly those facts plus the prose (outbuildings,
condition) that scoring keys off. So ``fetch_detail`` follows that link, and
``options.expose_pdf`` exists for an operator who cannot pay the bandwidth.

BLfD disclaims the accuracy of what owners submit, which is modelled as a
*reliability* below 1.0 in the registry - never as a lower role. Accuracy and
provenance are different questions.

A real capture of the search CGI (2026-09-03, see
``tests/fixtures/html/denkmalboerse_search_cgi.html``) settled two questions
that used to be unverified. First, the shape: one response holds the entire
catalogue as a ``<table>`` of ``<tr>`` rows, no pagination - so ``discover()``
row-scans rather than anchor-scans, and a genuinely complete walk of it can
leave ``enumeration_complete`` true (see ``mark_enumeration_incomplete``
below). Second, the last ``<td>`` of every row carries the object's
Regierungsbezirk. On that capture the bundled gazetteer (Upper-Bavaria-only)
skips very few rows outright, while the Regierungsbezirk alone - checked
before the gazetteer, on every row - skips most of the 237 objects with
certainty. So it runs first, as a wider and more reliable gate; the gazetteer
stays as a second, narrower one for the towns it does recognise within scope.
See ``docs/SOURCES.md`` for the counts.

A Bezirk gate that only ever *saves* a fetch must never make it possible for
a still-live listing to be read as withdrawn: a row either pre-filter skips
is still recorded in ``self.enumerated_urls`` (see ``SourceAdapter``), because
``hofradar.pipeline.runner`` needs to tell "skipped this run" apart from
"the source stopped carrying it" before it ever asks
``hofradar.lifecycle.mark_missing`` a question about absence.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser, Node

from hofradar.contracts import RawListing
from hofradar.geo import town_in_radius
from hofradar.sources.adapters._htmlutil import raw_listing_from_html
from hofradar.sources.adapters._pdfutil import (
    WARNING_PDF_UNAVAILABLE,
    PdfTooLarge,
    PdfUnavailable,
    PdfUnreadable,
    extract_pdf_text,
    find_pdf_links,
    looks_like_pdf,
    merge_pdf_into_listing,
)
from hofradar.sources.base import SourceAdapter
from hofradar.sources.exceptions import SourceDiscoveryError

if TYPE_CHECKING:  # pragma: no cover
    from hofradar.config import KeywordConfig, SearchProfile

logger = logging.getLogger(__name__)

#: The search CGI. It is the only index the site offers; there is no sitemap.
SEARCH_PATH = "/cgi-bin/fts_search_verkauf.pl"
#: Object detail pages: /information-service/denkmalboerse/objekte/007148/index.html
OBJECT_HREF_RE = re.compile(r"/information-service/denkmalboerse/objekte/(\d{6})/index\.html")
#: Titles read "Historische Hofstelle in Reichenschwand - Leuzenberg" (a district
#: suffix set off by a *spaced* dash), "Bauernhaus in Rosenheim, Oberbayern" (a
#: district/region set off by a comma), or "Pfarrhof in Neumarkt-Sankt Veit" (a
#: town name that itself contains an unspaced hyphen). Only a dash with
#: whitespace on both sides, or a comma, ends the town - so a hyphenated town
#: name is never truncated, and a comma-separated district still is. Trailing
#: prose with none of those separators (e.g. "in Schwindegg zu verkaufen") is
#: not specially handled: the false positive just costs one extra fetch the
#: gazetteer would otherwise have saved, and guessing at a stoplist of German
#: verb phrases is not worth the regex complexity it would add.
TITLE_TOWN_RE = re.compile(r"\bin\s+([^,]+?)(?=\s[-–—]\s|,|$)", re.IGNORECASE)

#: Every Regierungsbezirk a row's last column can legitimately name. Used to
#: tell a genuinely out-of-scope Bezirk apart from a value the pre-filter does
#: not recognise (empty, mangled, a template change) - the latter must fall
#: through and fetch, exactly like a gazetteer-unknown town does, rather than
#: being silently discarded on a label this adapter has never seen.
BAVARIAN_REGIERUNGSBEZIRKE: frozenset[str] = frozenset(
    {
        "Oberbayern",
        "Niederbayern",
        "Oberpfalz",
        "Oberfranken",
        "Mittelfranken",
        "Unterfranken",
        "Schwaben",
    }
)

#: Regierungsbezirke in scope absent an ``options.regierungsbezirke`` override.
#: Tied to the *default* search profile's ``air_km_max`` (80 km, see
#: config/search.yaml), not just to "near the origin": Landshut (Nieder-
#: bayern) is 73.8 km out and Landsberg am Lech (Schwaben) is 73.1 km out by
#: this project's own ``haversine_km``, both inside that radius, so both
#: Bezirke belong here even though the search is centred well inside Ober-
#: bayern. A pre-filter may only ever save a fetch, never lose a property -
#: an operator who raises ``air_km_max`` well past 80 km should widen
#: ``options.regierungsbezirke`` to match, since this constant does not
#: derive itself from the profile at runtime.
DEFAULT_IN_SCOPE_REGIERUNGSBEZIRKE: tuple[str, ...] = ("Oberbayern", "Niederbayern", "Schwaben")


#: BLfD serves every exposé PDF out of its media area, at
#: /mam/information_und_service/denkmal_boerse/<bezirk>/<name>.pdf. A detail
#: page can carry a second PDF (a Denkmalliste extract, a broker's own flyer),
#: so the media-area path is what identifies the exposé among them.
MAM_PATH_PREFIX = "/mam/"

#: The adapter option that switches the exposé download off. One exposé is
#: 2-14 MB and is re-fetched every run, which an operator on a metered line or
#: a Raspberry Pi may not want to pay for - at the cost of the facts below.
OPTION_EXPOSE_PDF = "expose_pdf"
DEFAULT_EXPOSE_PDF = True

#: What the exposé is called in the dossier's "Dokumente" list.
EXPOSE_DOCUMENT_TITLE = "Exposé (PDF)"

#: German, because these reach the reader on the dossier: a listing whose
#: facts could not be read must say so rather than look like a thin advert
#: (see the module docstring of ``_pdfutil``).
WARNING_PDF_HTTP_STATUS = (
    "Exposé-PDF konnte nicht geladen werden (HTTP {status}) – "
    "Angaben stammen nur aus der Kurzinfo"
)
WARNING_PDF_NOT_A_PDF = (
    "Exposé-Link lieferte kein PDF – Angaben stammen nur aus der Kurzinfo"
)
WARNING_PDF_UNREACHABLE = (
    "Exposé-PDF konnte nicht abgerufen werden – Angaben stammen nur aus der Kurzinfo"
)
WARNING_PDF_TOO_LARGE = (
    "Exposé-PDF ist zu groß und wurde nicht gelesen – Angaben stammen nur aus der Kurzinfo"
)
WARNING_PDF_UNREADABLE = (
    "Exposé-PDF konnte nicht gelesen werden – Angaben stammen nur aus der Kurzinfo"
)


def _resolve_in_scope_bezirke(options: dict[str, object]) -> frozenset[str]:
    """The effective in-scope Regierungsbezirk set for this run.

    ``options`` absent the key at all means "no override" -> the default.
    An explicit empty list is a deliberate "nothing in scope", not the same
    thing, so it is honoured as given rather than silently falling back.
    Matching is case-insensitive so an operator typo like "oberbayern" still
    lines up with the row value "Oberbayern" - the alternative is a filter
    that looks configured but matches nothing, silently skipping every row
    in Bavaria, which is exactly what a pre-filter may never do. Only when
    *none* of the configured values match any of Bavaria's seven at all -
    a value that is not a case variant, just wrong - does this fall back to
    the default, loudly, rather than leave the source silently empty.
    """
    if "regierungsbezirke" not in options:
        return frozenset(DEFAULT_IN_SCOPE_REGIERUNGSBEZIRKE)
    configured = options["regierungsbezirke"] or []
    folded = {str(value).casefold() for value in configured}
    in_scope = frozenset(b for b in BAVARIAN_REGIERUNGSBEZIRKE if b.casefold() in folded)
    if configured and not in_scope:
        logger.warning(
            "options.regierungsbezirke=%r matched none of Bavaria's seven "
            "Regierungsbezirke - falling back to the default %s",
            configured,
            DEFAULT_IN_SCOPE_REGIERUNGSBEZIRKE,
        )
        return frozenset(DEFAULT_IN_SCOPE_REGIERUNGSBEZIRKE)
    return in_scope


def _town_from_title(title: str | None) -> str | None:
    if not title:
        return None
    match = TITLE_TOWN_RE.search(title)
    return match.group(1).strip() if match else None


def _object_anchor(row: Node) -> Node | None:
    """The row's one anchor whose href is an object detail link, if any.

    A row is not assumed to have exactly one ``<a>``: the price cell has none,
    the info cell has exactly the object link. Scanning rather than indexing
    keeps this working if BLfD ever adds another link (a PDF exposé, a map) to
    the row.
    """
    for candidate in row.css("a"):
        href = candidate.attributes.get("href") or ""
        if OBJECT_HREF_RE.search(href):
            return candidate
    return None


#: A "PLZ Ort" address paragraph starts with a 5-digit postcode. Roughly 1 in
#: 5 rows opens its info cell with a "Kaufpreis: ..." paragraph instead (no
#: address line at all) - ``row.css_first("p")`` would silently return that
#: price text there, which is truthy and so would defeat the title fallback
#: below without ever tripping it. Scanning every ``<p>`` for the one that
#: actually looks like an address, instead of trusting position, is what
#: keeps that fallback reachable.
_ADDRESS_PARAGRAPH_RE = re.compile(r"^\d{5}\s")


def _town_from_row(row: Node, title: str | None) -> str | None:
    """The row's "PLZ Ort" address line, falling back to the title heuristics.

    The address paragraph (e.g. "84453 Mühldorf am Inn") needs no dash/comma
    splitting and matches the gazetteer far more often than a parsed title
    does - a title's "Mühldorf a. Inn" does not match the gazetteer's
    "Muehldorf am Inn", but the row's own address line does. The title is the
    fallback for a row that has no address paragraph, which real rows do.
    """
    for paragraph in row.css("p"):
        text = paragraph.text(strip=True)
        if _ADDRESS_PARAGRAPH_RE.match(text):
            return text
    return _town_from_title(title)


def _expose_pdf_url(html: str, page_url: str) -> str | None:
    """The object's exposé PDF link - BLfD's own media-area asset preferred.

    A page may link more than one PDF, so the ``/mam/`` path wins rather than
    document order. Any other PDF link is taken only when there is no
    media-area one at all: a template that moved the asset elsewhere should
    still yield the exposé rather than silently leave the listing thin.
    """
    links = find_pdf_links(html, page_url)
    if not links:
        return None
    for absolute, _text in links:
        if urlsplit(absolute).path.startswith(MAM_PATH_PREFIX):
            return absolute
    return links[0][0]


def _regierungsbezirk_from_row(row: Node) -> str | None:
    """The row's last ``<td>`` - the Regierungsbezirk column BLfD appends."""
    cells = row.css("td")
    if not cells:
        return None
    text = cells[-1].text(strip=True)
    return text or None


class DenkmalboerseAdapter(SourceAdapter):
    """Fetches the search CGI's result list and each object's static detail page.

    Parsing stays at the string level throughout - see ``fetch_detail``. The
    two pre-filters in ``discover()`` read a Bezirk label and a town name for
    routing decisions only; turning "auf Anfrage" into a typed price, or a
    description into keyword hits, is ``hofradar.normalize``'s job, not this
    adapter's.
    """

    key = "denkmalboerse"

    async def discover(
        self, profile: SearchProfile, keywords: KeywordConfig
    ) -> AsyncIterator[RawListing]:
        if not self.base_url:
            raise SourceDiscoveryError(f"{self.key}: no base_url configured")

        self.begin_enumeration()
        index_url = urljoin(self.base_url, SEARCH_PATH)

        try:
            response = await self.client.get(index_url)
        except Exception as exc:  # noqa: BLE001 - reported below, not left opaque
            self.mark_enumeration_incomplete(f"search CGI request failed: {exc}")
            raise SourceDiscoveryError(
                f"{self.key}: could not reach the search CGI: {exc}"
            ) from exc
        if response.status_code >= 400:
            self.mark_enumeration_incomplete(
                f"search CGI returned HTTP {response.status_code}"
            )
            raise SourceDiscoveryError(
                f"{self.key}: search CGI returned HTTP {response.status_code}"
            )

        tree = HTMLParser(response.text)
        rows = tree.css("tbody tr")

        in_scope = _resolve_in_scope_bezirke(self.options)

        seen: set[str] = set()
        object_count = 0
        any_detail_failed = False
        rows_walked_fully = False

        try:
            for row in rows:
                anchor = _object_anchor(row)
                if anchor is None:
                    continue
                href = anchor.attributes.get("href") or ""
                match = OBJECT_HREF_RE.search(href)
                if match is None:
                    continue
                object_id = match.group(1)
                if object_id in seen:
                    continue
                seen.add(object_id)
                object_count += 1

                detail_url = urljoin(self.base_url, match.group(0))
                # Examined this run whether or not a pre-filter below skips
                # its fetch: a filtered row is a choice not to re-check
                # something already on file, never a claim the source
                # withdrew it. See SourceAdapter.enumerated_urls.
                self.record_enumerated_url(detail_url)

                # The primary pre-filter: on the 2026-09-03 capture this
                # alone skips most rows with certainty, dwarfing what the
                # gazetteer saves on the same response (see
                # docs/SOURCES.md). Only a value this adapter actually
                # recognises as a Regierungsbezirk may reject a row -
                # anything else (missing, mangled, a template change) falls
                # through, same as the gazetteer's None.
                bezirk = _regierungsbezirk_from_row(row)
                if bezirk in BAVARIAN_REGIERUNGSBEZIRKE and bezirk not in in_scope:
                    logger.debug(
                        "%s: skipping %s - Regierungsbezirk %r out of scope",
                        self.key,
                        object_id,
                        bezirk,
                    )
                    continue

                title = anchor.text(strip=True)
                # The second, narrower pre-filter. It may only ever save a
                # fetch: False means the gazetteer is sure this is outside
                # the radius; None means it has never heard of the place,
                # which is precisely where a hamlet with a farmstead lives -
                # so None still falls through to a fetch.
                if town_in_radius(_town_from_row(row, title), profile) is False:
                    logger.debug(
                        "%s: skipping %s (%s) - outside radius", self.key, object_id, title
                    )
                    continue

                listing = await self.fetch_detail(detail_url)
                if listing is None:
                    any_detail_failed = True
                    continue
                yield listing
            rows_walked_fully = True
        finally:
            # Invariant 4b: absence needs a complete enumeration, not just
            # permission. A consumer that stops draining early (a caller
            # that breaks out of the loop) tears this generator down via
            # GeneratorExit at the last yield, same as any other early exit
            # - the ``finally`` here is what still runs the check below in
            # that case, rather than silently leaving the True that
            # begin_enumeration() set. A zero-row parse is indistinguishable
            # from a template change that broke every selector above, and a
            # detail page that failed to fetch means this run did not
            # actually see everything the index promised - either way, this
            # run's silence must not be read as "everything else was
            # removed".
            if not rows_walked_fully:
                self.mark_enumeration_incomplete(
                    "discover() was not fully consumed - only "
                    f"{object_count} row(s) were examined"
                )
            elif object_count == 0:
                self.mark_enumeration_incomplete(
                    "parsed zero object rows from the search CGI response - "
                    "possibly a template change"
                )
            elif any_detail_failed:
                self.mark_enumeration_incomplete(
                    "one or more detail fetches failed during this run"
                )

    async def fetch_detail(self, url: str) -> RawListing | None:
        try:
            response = await self.client.get(url)
        except Exception as exc:  # noqa: BLE001 - one bad detail page must not abort the run
            logger.warning("%s: fetch_detail failed for %s: %s", self.key, url, exc)
            return None
        if response.status_code >= 400:
            return None
        listing = raw_listing_from_html(
            self.key, url, response.text, http_status=response.status_code
        )
        match = OBJECT_HREF_RE.search(url)
        if match is not None:
            listing.external_id = match.group(1)
        # Owners publish their own contact details in the exposé; the Amt does
        # not broker the sale and there is no Chiffre intermediary.
        listing.contact_kind = "private"
        if bool(self.options.get(OPTION_EXPOSE_PDF, DEFAULT_EXPOSE_PDF)):
            # Never inside the try/except above and never a reason to return
            # None: the detail page itself was read, so the listing exists.
            # Only the enrichment can fail here, and a failed enrichment must
            # not travel to mark_enumeration_incomplete - invariant 4b is
            # about whether this run saw everything the index promised, not
            # about how much of each advert could be read.
            await self._attach_expose_pdf(listing, response.text, url)
        return listing

    async def _attach_expose_pdf(self, listing: RawListing, html: str, page_url: str) -> None:
        """Read the exposé PDF behind the page's "zum Exposé" link into the listing.

        Why this exists: the detail page is a Kurzinfo box and a link, and the
        facts scoring keys off are in the PDF. On a live 30-object in-scope
        sample, 29 had an exposé, while the HTML alone left rooms empty on 30
        of 30, usable area on 24 and living area on 9 - that is the "k. A."
        the reader sees.

        Why every failure is said out loud: a listing enriched from nothing
        looks exactly like a genuinely thin advert. Each failure appends a
        German warning to the listing instead, and the listing is still
        returned - the Kurzinfo is real, it is just all there is.
        """
        pdf_url = _expose_pdf_url(html, page_url)
        if pdf_url is None:
            logger.debug("%s: no exposé PDF linked on %s", self.key, page_url)
            return

        try:
            response = await self.client.get(pdf_url)
        except Exception as exc:  # noqa: BLE001 - a missing exposé is a warning, not a failure
            logger.warning("%s: exposé fetch failed for %s: %s", self.key, pdf_url, exc)
            listing.warnings.append(WARNING_PDF_UNREACHABLE)
            return
        if not response.is_success:
            logger.warning(
                "%s: exposé %s returned HTTP %s", self.key, pdf_url, response.status_code
            )
            listing.warnings.append(
                WARNING_PDF_HTTP_STATUS.format(status=response.status_code)
            )
            return
        if not looks_like_pdf(response.content):
            # An error page served with HTTP 200, or a link that now points at
            # a landing page - either way there is no document behind it.
            logger.warning("%s: exposé %s did not return a PDF", self.key, pdf_url)
            listing.warnings.append(WARNING_PDF_NOT_A_PDF)
            return

        try:
            text = extract_pdf_text(response.content)
        except PdfUnavailable:
            logger.warning("%s: pypdf is not installed - %s was not read", self.key, pdf_url)
            listing.warnings.append(WARNING_PDF_UNAVAILABLE)
            return
        except PdfTooLarge as exc:
            logger.warning("%s: exposé %s is too large: %s", self.key, pdf_url, exc)
            listing.warnings.append(WARNING_PDF_TOO_LARGE)
            return
        except PdfUnreadable as exc:
            logger.warning("%s: exposé %s could not be read: %s", self.key, pdf_url, exc)
            listing.warnings.append(WARNING_PDF_UNREADABLE)
            return

        # A PDF with no text layer carries its own warning in text.warnings,
        # which merge_pdf_into_listing moves onto the listing.
        merge_pdf_into_listing(
            listing, text, document_url=pdf_url, document_title=EXPOSE_DOCUMENT_TITLE
        )
