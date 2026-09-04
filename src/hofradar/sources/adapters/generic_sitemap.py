"""Polite sitemap.xml crawl of small broker sites that publish one.

A sitemap index is resolved recursively (capped in depth and fan-out so one
misconfigured site cannot turn into an unbounded crawl), the leaf URLs are
filtered down to whatever looks like a listing page, and each surviving URL
is fetched once through :meth:`fetch_detail`. ``options.max_pages`` bounds
the total number of detail pages fetched in a single ``discover()`` run.

This adapter's role is ``primary`` and it claims ``enumerates=True``, so per
invariant 4b every way this run can fall short of a complete enumeration has
to say so - otherwise ``hofradar.lifecycle.mark_missing`` reads the gap as
"the seller withdrew it". The ways it can fall short are: the page budget
running out, a sitemap that could not be read, a malformed ``options.sites``
entry, and a detail page that returned nothing or raised. A flaky single
detail page is the dangerous one, because it looks exactly like a normal run
minus one listing. Every leaf URL the sitemap offered is also recorded via
``record_enumerated_url`` **before** the listing-pattern filter runs, for the
same reason the Denkmalbörse adapter records its pre-filtered rows: a URL this
run chose not to fetch (or stopped recognising, because the operator changed
``options.pattern``) is a routing decision, never a claim the site withdrew
it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from typing import Any

from selectolax.parser import HTMLParser

from hofradar.config import KeywordConfig, SearchProfile
from hofradar.contracts import RawListing
from hofradar.sources.adapters._htmlutil import is_utility_url, raw_listing_from_html
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
        # Before the empty-sites return, not after: a run that searched
        # nothing must leave this adapter in a state that cannot license
        # absence detection, and the previous ordering left whatever the last
        # run happened to set (True by default) standing.
        self.begin_enumeration()
        sites: list[Any] = list(self.options.get("sites") or [])
        if not sites:
            logger.info("%s: no sites configured (options.sites) - nothing to discover", self.key)
            self.mark_enumeration_incomplete("no sites configured; nothing was crawled")
            return

        default_pattern = self.options.get("pattern")
        max_pages = int(self.options.get("max_pages", DEFAULT_MAX_PAGES))
        budget = max_pages
        any_readable = False
        sites_walked_fully = False

        try:
            for entry in sites:
                if budget <= 0:
                    self.mark_enumeration_incomplete(f"max_pages={max_pages} reached")
                    break
                sitemap_url, pattern_str = _site_config(entry)
                if not sitemap_url:
                    logger.warning("%s: skipping malformed site entry: %r", self.key, entry)
                    self.mark_enumeration_incomplete(
                        f"malformed options.sites entry {entry!r} was never crawled"
                    )
                    continue
                pattern = (
                    re.compile(pattern_str or default_pattern)
                    if (pattern_str or default_pattern)
                    else None
                )

                try:
                    urls = await self._collect_sitemap_urls(sitemap_url)
                except Exception as exc:  # noqa: BLE001 - one bad sitemap must not abort the run
                    logger.warning("%s: could not read sitemap %s: %s", self.key, sitemap_url, exc)
                    self.mark_enumeration_incomplete(
                        f"sitemap {sitemap_url} could not be read: {exc}"
                    )
                    continue
                any_readable = True

                for url in urls:
                    if budget <= 0:
                        self.mark_enumeration_incomplete(f"max_pages={max_pages} reached")
                        break
                    # Recorded before every filter: examined this run whether
                    # or not it was fetched. See the module docstring.
                    self.record_enumerated_url(url)
                    if is_utility_url(url):
                        # A sitemap lists the site, not its inventory: with no
                        # options.pattern configured, /merkliste and /impressum
                        # are fetched and ingested exactly like an advert
                        # (GitHub issue #10). Skipping them is a routing
                        # decision like the pattern below, which is why it
                        # happens after the URL was recorded as enumerated.
                        continue
                    if pattern is not None and not pattern.search(url):
                        continue
                    try:
                        listing = await self.fetch_detail(url)
                    except Exception as exc:  # noqa: BLE001 - one bad page must not stop the crawl
                        logger.warning("%s: skipping %s: %s", self.key, url, exc)
                        self.mark_enumeration_incomplete(
                            f"detail fetch raised for listed URL {url}: {exc}"
                        )
                        continue
                    budget -= 1
                    if listing is None:
                        # The sitemap listed this page; fetch_detail returning
                        # None means we never actually looked at it, so its
                        # absence from the observed set is not evidence of a
                        # removal.
                        self.mark_enumeration_incomplete(
                            f"detail fetch failed for listed URL {url}"
                        )
                        continue
                    yield listing
            sites_walked_fully = True
        finally:
            # A caller that stops draining tears this generator down at the
            # last yield; without the finally the run would keep the True
            # begin_enumeration() set, exactly as denkmalboerse and ovbimmo
            # guard against.
            if not sites_walked_fully:
                self.mark_enumeration_incomplete(
                    "discover() was not fully consumed - "
                    f"{len(self.enumerated_urls)} URL(s) were examined"
                )

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
