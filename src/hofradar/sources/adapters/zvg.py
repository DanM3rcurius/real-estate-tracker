"""ZVG-Portal.de - the official German court foreclosure register.

Highest-signal hidden source in the registry: a Zwangsversteigerung never
shows up on the usual portals, and the Verkehrswert (court-assessed value) is
frequently well under market. The site's markup has drifted across its
history and offers no stable CSS hooks, so :func:`parse_zvg_results` never
relies on position or class names - it reads the results table by matching
column *header text* (Aktenzeichen, Verkehrswert, Amtsgericht, Ort,
Versteigerungstermin) and tolerates a row missing any of those cells.

The search form's exact field names are the portal's, not ours, and can
drift; ``discover()`` isolates that guesswork behind one POST so only this
module needs updating if the site changes.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urljoin

from selectolax.parser import HTMLParser

from hofradar.config import KeywordConfig, SearchProfile
from hofradar.contracts import RawListing
from hofradar.sources.adapters._htmlutil import raw_listing_from_html
from hofradar.sources.base import SourceAdapter
from hofradar.sources.exceptions import SourceDiscoveryError

logger = logging.getLogger(__name__)

_UMLAUT_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

#: Folded header text -> a field we care about. Values starting with "_" are
#: combined into other RawListing fields rather than mapped 1:1.
_HEADER_FIELD_MAP: dict[str, str] = {
    "aktenzeichen": "external_id",
    "az": "external_id",
    "amtsgericht": "_amtsgericht",
    "gericht": "_amtsgericht",
    "verkehrswert": "price_raw",
    "versteigerungstermin": "_termin",
    "termin": "_termin",
    "versteigerungsdatum": "_termin",
    "ort": "_ort",
    "objektort": "_ort",
    "lage": "_ort",
    "objekt": "_objekt",
    "objektart": "_objekt",
    "beschreibung": "_objekt",
    "bezeichnung": "_objekt",
}


def _fold(text: str) -> str:
    return text.strip().lower().translate(_UMLAUT_FOLD)


def parse_zvg_results(
    html: str, *, source_key: str, base_url: str | None = None
) -> list[RawListing]:
    """Parse a ZVG-Portal search-results page into RawListings.

    Deliberately tolerant: any table whose header row names an
    "Aktenzeichen" column is treated as a results table, columns are located
    by header text (never by position), and a row missing a cell just
    contributes fewer fields rather than being dropped.
    """
    tree = HTMLParser(html)
    listings: list[RawListing] = []

    for table in tree.css("table"):
        header_cells = table.css("thead th") or table.css("tr:first-child th")
        if not header_cells:
            first_row = table.css_first("tr")
            header_cells = first_row.css("td") if first_row is not None else []
        if not header_cells:
            continue

        header_map: dict[int, str] = {}
        for idx, cell in enumerate(header_cells):
            field = _HEADER_FIELD_MAP.get(_fold(cell.text(strip=True)))
            if field:
                header_map[idx] = field
        if "external_id" not in header_map.values():
            continue  # not a results table we recognise

        body_rows = table.css("tbody tr")
        if not body_rows:
            all_rows = table.css("tr")
            body_rows = all_rows[1:] if len(all_rows) > 1 else []

        for row in body_rows:
            cells = row.css("td")
            if not cells:
                continue

            values: dict[str, str] = {}
            link_href: str | None = None
            for idx, cell in enumerate(cells):
                field = header_map.get(idx)
                if field is not None:
                    text = cell.text(strip=True)
                    if text:
                        values[field] = text
                if link_href is None:
                    anchor = cell.css_first("a[href]")
                    if anchor is not None:
                        link_href = anchor.attributes.get("href")
            if not values:
                continue

            external_id = values.get("external_id")
            location_raw = ", ".join(
                p for p in (values.get("_amtsgericht"), values.get("_ort")) if p
            ) or None

            url = urljoin(base_url or "", link_href) if link_href else None
            if not url:
                if external_id and base_url:
                    url = f"{base_url.rstrip('/')}/index.php?az={quote(external_id)}"
                else:
                    logger.warning(
                        "zvg: skipping row with no detail link and no Aktenzeichen"
                    )
                    continue

            extra: dict[str, Any] = {"is_foreclosure": True}
            termin = values.get("_termin")
            if termin:
                extra["versteigerungstermin"] = termin

            listings.append(
                RawListing(
                    source_key=source_key,
                    url=url,
                    title=values.get("_objekt") or f"Zwangsversteigerung {external_id or ''}".strip(),
                    price_raw=values.get("price_raw"),
                    location_raw=location_raw,
                    external_id=external_id,
                    fetched_at=datetime.now(UTC),
                    extra=extra,
                )
            )

    return listings


class ZvgAdapter(SourceAdapter):
    """Searches the Bavarian ZVG-Portal and parses the results table."""

    SEARCH_PATH = "/index.php"

    async def discover(
        self, profile: SearchProfile, keywords: KeywordConfig
    ) -> AsyncIterator[RawListing]:
        if not self.base_url:
            raise SourceDiscoveryError(f"{self.key}: no base_url configured")

        search_url = self.base_url.rstrip("/") + self.SEARCH_PATH
        payload = {
            "bundesland": self.options.get("land_abk", "by"),
            "objektart": self.options.get("objektart", "L"),  # L = Land-/Forstwirtschaft
            "submit": "Suchen",
        }
        try:
            response = await self.client.post(search_url, data=payload)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            raise SourceDiscoveryError(f"{self.key}: search request failed: {exc}") from exc

        try:
            listings = parse_zvg_results(response.text, source_key=self.key, base_url=self.base_url)
        except Exception as exc:  # noqa: BLE001 - a markup change must not crash the pipeline
            raise SourceDiscoveryError(f"{self.key}: could not parse search results: {exc}") from exc

        if not listings:
            logger.info("%s: search returned no rows", self.key)
        for listing in listings:
            yield listing

    async def fetch_detail(self, url: str) -> RawListing | None:
        try:
            response = await self.client.get(url)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: fetch_detail failed for %s: %s", self.key, url, exc)
            return None
        listing = raw_listing_from_html(self.key, url, response.text, http_status=response.status_code)
        listing.extra["is_foreclosure"] = True
        return listing
