"""The paste-ingest adapter: no crawling, just "here is a listing".

This is the source that makes the product useful on day one. The web UI's
"paste a listing" box hands it either a URL (``ingest_url``) or the full text
of a pasted exposé (``ingest_text``), and it turns that into a RawListing -
same shape as anything a crawler would have produced, so it flows through
the rest of the pipeline unchanged.

Role is PRIMARY: a human looked at the actual listing and pasted it in, which
is at least as trustworthy as a crawler fetching the same page automatically.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from hofradar.config import KeywordConfig, SearchProfile
from hofradar.contracts import RawListing
from hofradar.sources.adapters._htmlutil import extract_labeled_fields, raw_listing_from_html
from hofradar.sources.base import SourceAdapter, text_indicates_gone

logger = logging.getLogger(__name__)

#: Heuristic for "this pasted blob is HTML, not plain exposé text".
_HTML_HINT_RE = re.compile(r"<\s*(html|body|div|p|h1|title|meta)\b", re.IGNORECASE)

#: Bare image URLs a human might paste alongside plain text (not inside <img>).
_BARE_IMAGE_URL_RE = re.compile(
    r"https?://\S+?\.(?:jpg|jpeg|png|webp|gif)(?:\?\S*)?", re.IGNORECASE
)

_MAX_PLAIN_TITLE_LEN = 300


def _looks_like_html(text: str) -> bool:
    return bool(_HTML_HINT_RE.search(text))


def _from_plain_text(source_key: str, url: str, text: str, *, http_status: int | None) -> RawListing:
    lines = [line.strip() for line in text.splitlines()]
    title = next((line for line in lines if line), None)
    if title and len(title) > _MAX_PLAIN_TITLE_LEN:
        title = title[:_MAX_PLAIN_TITLE_LEN].rstrip() + "..."

    image_urls = list(dict.fromkeys(_BARE_IMAGE_URL_RE.findall(text)))
    labeled = extract_labeled_fields(text)

    return RawListing(
        source_key=source_key,
        url=url,
        title=title,
        description=text.strip() or None,
        image_urls=image_urls,
        http_status=http_status,
        fetched_at=datetime.now(UTC),
        **labeled,
    )


class ManualAdapter(SourceAdapter):
    """Paste-ingest. ``discover()`` yields nothing - ingestion is user-triggered."""

    async def discover(
        self, profile: SearchProfile, keywords: KeywordConfig
    ) -> AsyncIterator[RawListing]:
        # Nothing to autonomously discover: this source only ever produces a
        # RawListing when the web UI calls ingest_text/ingest_url directly.
        return
        yield  # pragma: no cover - keeps this an async generator

    async def fetch_detail(self, url: str) -> RawListing | None:
        return await self.ingest_url(url)

    def ingest_text(self, url: str, text: str) -> RawListing:
        """Turn a pasted exposé (or a hand-pasted HTML source) into a RawListing."""
        if _looks_like_html(text):
            return raw_listing_from_html(self.key, url, text)
        return _from_plain_text(self.key, url, text, http_status=None)

    async def ingest_url(self, url: str) -> RawListing | None:
        """Fetch ``url`` (politely, through the shared client) and ingest it."""
        try:
            response = await self.client.get(url)
        except Exception as exc:  # noqa: BLE001 - a failed paste-fetch must not crash the UI
            logger.warning("%s: could not fetch %s: %s", self.key, url, exc)
            return None
        listing = raw_listing_from_html(self.key, url, response.text, http_status=response.status_code)
        listing.listing_visible = not (
            response.status_code in (404, 410) or text_indicates_gone(response.text)
        )
        return listing
