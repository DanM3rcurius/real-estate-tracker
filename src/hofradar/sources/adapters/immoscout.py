"""ImmobilienScout24 - bot-defended; prefer official API/partner access if you have it.

ImmoScout24's terms of service restrict automated access and the site is
actively bot-defended. **This adapter is disabled by default**
(``enabled: false`` in config/sources.yaml) and is meant only for personal,
low-rate use run from your own machine - if you have (or can get) official
API/partner access instead, use that rather than enabling this.

The search-URL construction and the detail parser below were built and are
tested against a fixture page authored for this repository (see
``tests/fixtures/html/``), not against the live site, so the exact markup
will drift over time. This module does NOT implement, and will never
implement, any bot-defence evasion: no CAPTCHA solving, no proxy rotation,
no browser fingerprint spoofing. A blocked request is a clean, recorded
failure (see ``hofradar.sources.adapters._botcheck``) - discover() stops
rather than trying to get around it. Enabling this adapter will likely
require you to supply your own logged-in session.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from urllib.parse import quote

from selectolax.parser import HTMLParser, Node

from hofradar.config import KeywordConfig, SearchProfile
from hofradar.contracts import RawListing
from hofradar.sources.adapters._botcheck import raise_if_blocked
from hofradar.sources.adapters._htmlutil import raw_listing_from_html
from hofradar.sources.base import SourceAdapter
from hofradar.sources.exceptions import SourceDiscoveryError

logger = logging.getLogger(__name__)

DEFAULT_MAX_QUERIES = 5


def build_search_url(base_url: str, query: str, *, page: int = 1) -> str:
    """ImmoScout24's search is a query-string search over a fixed result-list path."""
    base = base_url.rstrip("/")
    params = f"searchType=region&query={quote(query)}"
    if page > 1:
        params += f"&pagenumber={page}"
    return f"{base}/Suche/de/wohnung-haus-kaufen?{params}"


def _card_url(card: Node, base_url: str) -> str | None:
    anchor = card.css_first("a[href]")
    if anchor is None:
        return None
    href = anchor.attributes.get("href")
    if not href:
        return None
    return href if href.startswith("http") else base_url.rstrip("/") + "/" + href.lstrip("/")


def parse_search_results(html: str, *, source_key: str, base_url: str) -> list[RawListing]:
    """Defensively parse a result-list page. Card selection tolerates a missing card."""
    tree = HTMLParser(html)
    listings: list[RawListing] = []
    cards = tree.css('[data-item="result-list-entry"]') or tree.css('[class*="result-list-entry"]')
    for card in cards:
        url = _card_url(card, base_url)
        if not url:
            continue
        title_node = card.css_first('[class*="title"]') or card.css_first("a[href]")
        price_node = card.css_first('[class*="price"]')
        address_node = card.css_first('[class*="address"]')
        listings.append(
            RawListing(
                source_key=source_key,
                url=url,
                title=title_node.text(strip=True) if title_node is not None else None,
                price_raw=price_node.text(strip=True) if price_node is not None else None,
                location_raw=address_node.text(strip=True) if address_node is not None else None,
            )
        )
    return listings


class ImmoscoutAdapter(SourceAdapter):
    """See module docstring: disabled by default, personal low-rate use only."""

    async def discover(
        self, profile: SearchProfile, keywords: KeywordConfig
    ) -> AsyncIterator[RawListing]:
        if not self.base_url:
            raise SourceDiscoveryError(f"{self.key}: no base_url configured")

        terms = list(dict.fromkeys(keywords.core or keywords.all_terms))
        max_queries = int(self.options.get("max_queries", DEFAULT_MAX_QUERIES))
        terms = terms[:max_queries]
        if not terms:
            logger.info("%s: no keyword vocabulary to search with", self.key)
            return

        any_readable = False
        for term in terms:
            url = build_search_url(self.base_url, term)
            try:
                response = await self.client.get(url)
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s: search request failed for %r: %s", self.key, term, exc)
                continue
            raise_if_blocked(response, source_key=self.key)
            if response.status_code >= 400:
                logger.warning("%s: search %r returned HTTP %d", self.key, term, response.status_code)
                continue
            any_readable = True
            for listing in parse_search_results(response.text, source_key=self.key, base_url=self.base_url):
                yield listing

        if not any_readable:
            raise SourceDiscoveryError(f"{self.key}: no search query returned a readable results page")

    async def fetch_detail(self, url: str) -> RawListing | None:
        try:
            response = await self.client.get(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: fetch_detail failed for %s: %s", self.key, url, exc)
            return None
        raise_if_blocked(response, source_key=self.key)
        if response.status_code >= 400:
            return None
        return raw_listing_from_html(self.key, url, response.text, http_status=response.status_code)
