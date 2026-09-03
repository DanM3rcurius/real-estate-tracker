"""Web-search discovery: find, never confirm.

Search coverage (a private seller not on any RSS feed, a small Sparkasse
landing page, a Facebook Marktplatz post) is worth having, but a search
result snippet is not a fetch of the actual listing - it can be stale,
cached, or already sold. Role is DISCOVERY, and every RawListing this
adapter yields is stamped ``extra["discovery_only"] = True`` so nothing
downstream can mistake a lead for a confirmation. Its ``verify()`` is not
overridden here: it inherits the base class's role gate, which raises
``NotSupported`` unconditionally for a DISCOVERY source.

No search backend is bundled. Set ``HOFRADAR_SEARCH_API_KEY`` and
``HOFRADAR_SEARCH_ENDPOINT`` to point this at whatever search API you have
access to - the endpoint is POSTed ``{"q": query, "limit": N}`` and is
expected to answer either a JSON list, or an object with a "results" list,
of ``{"title", "url", "snippet"}`` objects; adapt ``_run_query`` if your
provider's shape differs. With neither variable set, ``discover()`` logs a
clear "no search backend configured" message and yields nothing.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from hofradar.config import KeywordConfig, SearchProfile
from hofradar.contracts import RawListing
from hofradar.sources.base import SourceAdapter

logger = logging.getLogger(__name__)

DEFAULT_MAX_QUERIES = 8
DEFAULT_RESULTS_PER_QUERY = 10


def build_queries(
    profile: SearchProfile, keywords: KeywordConfig, *, max_queries: int = DEFAULT_MAX_QUERIES
) -> list[str]:
    """Combine the core keyword vocabulary with the search region into queries.

    Kept simple and inspectable on purpose: one core term plus the search
    centre's name, so every query is legible on its own in a log line.
    """
    core_terms = list(dict.fromkeys(keywords.core or keywords.all_terms))
    if not core_terms:
        return []
    region = profile.center.name or ""
    queries: list[str] = []
    for term in core_terms:
        queries.append(f'"{term}" {region}'.strip())
        if len(queries) >= max_queries:
            break
    return queries


class WebSearchAdapter(SourceAdapter):
    """DISCOVERY role: may find a property, never confirm it. See module docstring."""

    #: Search results are a sample, never an inventory.
    enumerates = False

    async def discover(
        self, profile: SearchProfile, keywords: KeywordConfig
    ) -> AsyncIterator[RawListing]:
        api_key = os.environ.get("HOFRADAR_SEARCH_API_KEY")
        endpoint = os.environ.get("HOFRADAR_SEARCH_ENDPOINT")
        if not api_key or not endpoint:
            logger.info(
                "%s: no search backend configured (set HOFRADAR_SEARCH_API_KEY and "
                "HOFRADAR_SEARCH_ENDPOINT) - yielding nothing",
                self.key,
            )
            return

        max_queries = int(self.options.get("max_queries", DEFAULT_MAX_QUERIES))
        queries = build_queries(profile, keywords, max_queries=max_queries)
        if not queries:
            logger.info("%s: no keyword vocabulary to build queries from", self.key)
            return

        for query in queries:
            try:
                results = await self._run_query(query, endpoint=endpoint, api_key=api_key)
            except Exception as exc:  # noqa: BLE001 - one bad query must not abort the run
                logger.warning("%s: search query failed (%r): %s", self.key, query, exc)
                continue
            for result in results:
                listing = self._result_to_listing(query, result)
                if listing is not None:
                    yield listing

    async def _run_query(self, query: str, *, endpoint: str, api_key: str) -> list[dict[str, Any]]:
        response = await self.client.post(
            endpoint,
            json={"q": query, "limit": DEFAULT_RESULTS_PER_QUERY},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results", []) if isinstance(payload, dict) else payload
        return results if isinstance(results, list) else []

    def _result_to_listing(self, query: str, result: dict[str, Any]) -> RawListing | None:
        url = result.get("url") or result.get("link")
        if not url:
            return None
        return RawListing(
            source_key=self.key,
            url=url,
            title=result.get("title"),
            description=result.get("snippet") or result.get("description"),
            fetched_at=datetime.now(UTC),
            extra={"discovery_only": True, "query": query},
        )

    async def fetch_detail(self, url: str) -> RawListing | None:
        # Enriching a discovery hit would mean fetching the third-party page
        # ourselves and presenting that as this source's finding - exactly
        # the authority a DISCOVERY source must not claim. A PRIMARY/LOCAL
        # adapter (or manual paste-ingest) is the right place for that fetch.
        return None
