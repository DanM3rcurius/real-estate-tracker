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

This adapter is not yet enabled: ``www.blfd.bayern.de`` is unreachable from
the environment this project was built in, so the terms/robots check
invariant 7 requires has not been run. See docs/SOURCES.md for the exact
commands and the checklist to run before ``config/sources.yaml`` may set
``enabled: true`` for this source.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

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


def _town_from_title(title: str | None) -> str | None:
    if not title:
        return None
    match = TITLE_TOWN_RE.search(title)
    return match.group(1).strip() if match else None


class DenkmalboerseAdapter(SourceAdapter):
    """Fetches the search CGI's result list and each object's static detail page.

    Parsing stays at the string level throughout - see ``fetch_detail``.
    Turning "auf Anfrage" into a typed price, or a description into keyword
    hits, is ``hofradar.normalize``'s job, not this adapter's.
    """

    key = "denkmalboerse"

    async def discover(
        self, profile: SearchProfile, keywords: KeywordConfig
    ) -> AsyncIterator[RawListing]:
        if not self.base_url:
            raise SourceDiscoveryError(f"{self.key}: no base_url configured")

        self.begin_enumeration()
        # This adapter has never seen a real response from the search CGI (see
        # the OUTSTANDING note in docs/SOURCES.md): whether a bare GET returns
        # every current object, only one page of a paginated list, or a search
        # *form* instead of results is unverified. Per invariant 4b, being
        # allowed to verify (role=primary) is not the same as having listed
        # everything, so this run's silence may never be read as "removed"
        # until a real capture confirms what a bare GET actually returns.
        self.mark_enumeration_incomplete(
            "search CGI response shape (pagination / form-vs-results) has "
            "never been captured against a real response"
        )

        index_url = urljoin(self.base_url, SEARCH_PATH)
        response = await self.client.get(index_url)
        tree = HTMLParser(response.text)

        seen: set[str] = set()
        for node in tree.css("a"):
            href = node.attributes.get("href") or ""
            match = OBJECT_HREF_RE.search(href)
            if match is None:
                continue
            object_id = match.group(1)
            if object_id in seen:
                continue
            seen.add(object_id)

            title = node.text(strip=True)
            # The pre-filter may only save a fetch. False means the gazetteer is
            # sure this is outside the radius; None means it has never heard of
            # the place, which is precisely where a hamlet with a farmstead
            # lives - so None still falls through to a fetch.
            if town_in_radius(_town_from_title(title), profile) is False:
                logger.debug(
                    "%s: skipping %s (%s) - outside radius", self.key, object_id, title
                )
                continue

            detail_url = urljoin(self.base_url, match.group(0))
            listing = await self.fetch_detail(detail_url)
            if listing is not None:
                yield listing

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
