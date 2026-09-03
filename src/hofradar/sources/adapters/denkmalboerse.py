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
Regierungsbezirk. On that capture the bundled gazetteer (Upper-Bavaria-only,
and a real GET only ever reaches it through a town name it already knows) does
not skip a single fetch, while the Regierungsbezirk alone - checked before the
gazetteer, on every row - skips the 194 objects outside the configured scope
with certainty. So it runs first, as a wider and more reliable gate; the
gazetteer stays as a second, narrower one for the towns it does recognise
within scope. See ``docs/SOURCES.md`` for the counts.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from selectolax.parser import HTMLParser, Node

from hofradar.contracts import RawListing
from hofradar.geo import town_in_radius
from hofradar.sources.adapters._htmlutil import raw_listing_from_html
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
#: This project's search profile is centred on Upper Bavaria (see
#: config/search.yaml), so that is the only district worth a detail fetch by
#: default.
DEFAULT_IN_SCOPE_REGIERUNGSBEZIRKE: tuple[str, ...] = ("Oberbayern",)


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


def _town_from_row(row: Node, title: str | None) -> str | None:
    """The row's "PLZ Ort" address line, falling back to the title heuristics.

    The address paragraph (e.g. "84453 Mühldorf am Inn") needs no dash/comma
    splitting and matches the gazetteer far more often than a parsed title
    does - a title's "Mühldorf a. Inn" does not match the gazetteer's
    "Muehldorf am Inn", but the row's own address line does. The title is only
    a fallback for a row that is ever missing its address paragraph.
    """
    address = row.css_first("p")
    address_text = address.text(strip=True) if address is not None else None
    return address_text or _town_from_title(title)


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

        in_scope = frozenset(
            self.options.get("regierungsbezirke") or DEFAULT_IN_SCOPE_REGIERUNGSBEZIRKE
        )

        seen: set[str] = set()
        object_count = 0
        any_detail_failed = False

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

            # The primary pre-filter: on the 2026-09-03 capture this alone
            # skips 194 of 237 rows with certainty, dwarfing what the
            # gazetteer saves on the same response (see docs/SOURCES.md). Only
            # a value this adapter actually recognises as a Regierungsbezirk
            # may reject a row - anything else (missing, mangled, a template
            # change) falls through, same as the gazetteer's None.
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
            # The second, narrower pre-filter. It may only ever save a fetch:
            # False means the gazetteer is sure this is outside the radius;
            # None means it has never heard of the place, which is precisely
            # where a hamlet with a farmstead lives - so None still falls
            # through to a fetch.
            if town_in_radius(_town_from_row(row, title), profile) is False:
                logger.debug(
                    "%s: skipping %s (%s) - outside radius", self.key, object_id, title
                )
                continue

            detail_url = urljoin(self.base_url, match.group(0))
            listing = await self.fetch_detail(detail_url)
            if listing is None:
                any_detail_failed = True
                continue
            yield listing

        # Invariant 4b: absence needs a complete enumeration, not just
        # permission. A zero-row parse is indistinguishable from a template
        # change that broke every selector above, and a detail page that
        # failed to fetch means this run did not actually see everything the
        # index promised - either way, this run's silence must not be read as
        # "everything else was removed".
        if object_count == 0:
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
        return listing
