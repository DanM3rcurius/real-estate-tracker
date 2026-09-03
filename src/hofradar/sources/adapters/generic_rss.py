"""RSS/Atom feed adapter for regional brokers who publish one.

Each configured feed URL turns into a batch of RawListings straight from the
feed entries (cheap - one request per feed). ``fetch_detail`` then does the
one-page GET that fills in whatever the entry didn't carry: full body text,
images, and any "Label: value" fields on the actual listing page.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import feedparser

from hofradar.config import KeywordConfig, SearchProfile
from hofradar.contracts import RawListing
from hofradar.sources.adapters._htmlutil import raw_listing_from_html
from hofradar.sources.base import SourceAdapter
from hofradar.sources.exceptions import SourceDiscoveryError

logger = logging.getLogger(__name__)


def _entry_image_urls(entry: Any) -> list[str]:
    """Pull image URLs from the standard syndication shapes feedparser exposes:
    plain RSS ``<enclosure>``, and Media RSS's ``media:content`` /
    ``media:thumbnail`` (namespace ``http://search.yahoo.com/mrss/`` - a
    widely-used syndication extension, not a feed vendor's own namespace).
    Reading a feed's own vendor-specific elements (e.g. classmarkets' ``cms:``
    namespace) does not belong here - that would make this adapter no longer
    generic; see docs/SOURCES.md for that call and what it costs.
    """
    urls: list[str] = []
    for enclosure in entry.get("enclosures") or []:
        href = enclosure.get("href") if isinstance(enclosure, dict) else None
        if href and href not in urls:
            urls.append(href)
    for media in entry.get("media_content") or []:
        url = media.get("url") if isinstance(media, dict) else None
        if url and url not in urls:
            urls.append(url)
    # feedparser exposes one media:thumbnail as a dict and several as a list
    # of dicts - normalise both shapes the same way as the sources above.
    thumbnails = entry.get("media_thumbnail") or []
    if isinstance(thumbnails, dict):
        thumbnails = [thumbnails]
    for thumb in thumbnails:
        url = thumb.get("url") if isinstance(thumb, dict) else None
        if url and url not in urls:
            urls.append(url)
    return urls


def _entry_to_listing(source_key: str, entry: Any) -> RawListing | None:
    url = entry.get("link")
    if not url:
        return None
    return RawListing(
        source_key=source_key,
        url=url,
        title=entry.get("title"),
        description=entry.get("summary") or entry.get("description"),
        external_id=entry.get("id") or entry.get("guid"),
        image_urls=_entry_image_urls(entry),
        source_date_raw=entry.get("published") or entry.get("updated"),
        fetched_at=datetime.now(UTC),
    )


class GenericRssAdapter(SourceAdapter):
    """Feeds are configured per broker in ``options.feeds`` (a list of URLs)."""

    #: A feed carries the latest N items. Item N+1 falling off the end is the
    #: feed being a feed, not the listing being gone.
    enumerates = False

    async def discover(
        self, profile: SearchProfile, keywords: KeywordConfig
    ) -> AsyncIterator[RawListing]:
        feeds: list[str] = list(self.options.get("feeds") or [])
        if not feeds:
            logger.info("%s: no feeds configured (options.feeds) - nothing to discover", self.key)
            return

        any_readable = False
        for feed_url in feeds:
            try:
                response = await self.client.get(feed_url)
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001 - one bad feed must not abort the run
                logger.warning("%s: could not fetch feed %s: %s", self.key, feed_url, exc)
                continue

            parsed = feedparser.parse(response.content)
            if parsed.bozo and not parsed.entries:
                logger.warning(
                    "%s: feed %s did not parse: %s", self.key, feed_url, parsed.get("bozo_exception")
                )
                continue
            any_readable = True

            for entry in parsed.entries:
                try:
                    listing = _entry_to_listing(self.key, entry)
                except Exception as exc:  # noqa: BLE001 - one malformed entry must not stop the feed
                    logger.warning("%s: skipping malformed entry in %s: %s", self.key, feed_url, exc)
                    continue
                if listing is not None:
                    yield listing

        if not any_readable:
            raise SourceDiscoveryError(
                f"{self.key}: none of {len(feeds)} configured feed(s) could be read"
            )

    async def fetch_detail(self, url: str) -> RawListing | None:
        try:
            response = await self.client.get(url)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: fetch_detail failed for %s: %s", self.key, url, exc)
            return None
        return raw_listing_from_html(self.key, url, response.text, http_status=response.status_code)
