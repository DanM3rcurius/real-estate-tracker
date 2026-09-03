"""Polite sitemap.xml crawl of small broker sites that publish one.

A sitemap index is resolved recursively (capped in depth and fan-out so one
misconfigured site cannot turn into an unbounded crawl), the leaf URLs are
filtered down to whatever looks like a listing page, and each surviving URL
is fetched once through :meth:`fetch_detail`. ``options.max_pages`` bounds
the total number of detail pages fetched in a single ``discover()`` run.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import Any

from selectolax.parser import HTMLParser

from hofradar.config import KeywordConfig, SearchProfile
from hofradar.contracts import RawListing
from hofradar.sources.adapters._htmlutil import raw_listing_from_html
from hofradar.sources.base import SourceAdapter
from hofradar.sources.exceptions import SourceDiscoveryError

logger = logging.getLogger(__name__)

DEFAULT_MAX_PAGES = 200
_MAX_SITEMAP_DEPTH = 3
_MAX_SUBSITEMAPS_PER_LEVEL = 50


def _site_config(entry: Any) -> tuple[str | None, str | None]:
    """Normalise one ``options.sites`` entry to (sitemap_url, pattern)."""
    if isinstance(entry, str):
        return entry, None
    if isinstance(entry, dict):
        return entry.get("sitemap_url") or entry.get("url"), entry.get("pattern")
    return None, None


class GenericSitemapAdapter(SourceAdapter):
    """Sites are configured in ``options.sites``: a list of sitemap URLs, or of
    ``{"sitemap_url": ..., "pattern": ...}`` objects for a per-site URL filter.
    ``options.pattern`` sets a default filter applied when a site has none of
    its own; with no pattern at all, every sitemap URL is considered.
    """

    async def discover(
        self, profile: SearchProfile, keywords: KeywordConfig
    ) -> AsyncIterator[RawListing]:
        sites: list[Any] = list(self.options.get("sites") or [])
        if not sites:
            logger.info("%s: no sites configured (options.sites) - nothing to discover", self.key)
            return

        default_pattern = self.options.get("pattern")
        max_pages = int(self.options.get("max_pages", DEFAULT_MAX_PAGES))
        budget = max_pages
        any_readable = False

        for entry in sites:
            if budget <= 0:
                break
            sitemap_url, pattern_str = _site_config(entry)
            if not sitemap_url:
                logger.warning("%s: skipping malformed site entry: %r", self.key, entry)
                continue
            pattern = re.compile(pattern_str or default_pattern) if (pattern_str or default_pattern) else None

            try:
                urls = await self._collect_sitemap_urls(sitemap_url)
            except Exception as exc:  # noqa: BLE001 - one bad sitemap must not abort the run
                logger.warning("%s: could not read sitemap %s: %s", self.key, sitemap_url, exc)
                continue
            any_readable = True

            for url in urls:
                if budget <= 0:
                    break
                if pattern is not None and not pattern.search(url):
                    continue
                try:
                    listing = await self.fetch_detail(url)
                except Exception as exc:  # noqa: BLE001 - one bad page must not stop the crawl
                    logger.warning("%s: skipping %s: %s", self.key, url, exc)
                    continue
                budget -= 1
                if listing is not None:
                    yield listing

        if not any_readable:
            raise SourceDiscoveryError(
                f"{self.key}: none of {len(sites)} configured site(s) had a readable sitemap"
            )

    async def _collect_sitemap_urls(
        self, sitemap_url: str, *, depth: int = 0, seen: set[str] | None = None
    ) -> list[str]:
        seen = seen if seen is not None else set()
        if sitemap_url in seen or depth > _MAX_SITEMAP_DEPTH:
            return []
        seen.add(sitemap_url)

        response = await self.client.get(sitemap_url)
        response.raise_for_status()
        tree = HTMLParser(response.content)

        if tree.css_first("sitemapindex") is not None:
            sub_sitemaps = [n.text(strip=True) for n in tree.css("sitemap loc")]
            sub_sitemaps = [u for u in sub_sitemaps if u][:_MAX_SUBSITEMAPS_PER_LEVEL]
            collected: list[str] = []
            for sub_url in sub_sitemaps:
                collected.extend(
                    await self._collect_sitemap_urls(sub_url, depth=depth + 1, seen=seen)
                )
            return collected

        return [n.text(strip=True) for n in tree.css("url loc") if n.text(strip=True)]

    async def fetch_detail(self, url: str) -> RawListing | None:
        try:
            response = await self.client.get(url)
            response.raise_for_status()
        except Exception as exc:  # noqa: BLE001
            logger.warning("%s: fetch_detail failed for %s: %s", self.key, url, exc)
            return None
        return raw_listing_from_html(self.key, url, response.text, http_status=response.status_code)
