"""Gemeinde-/Amtsblätter (PDF): where a Chiffre ad hides when it has no URL at all.

Municipal and district bulletins publish their classifieds as one PDF per
issue with no per-ad link - the only way to find "Alter Bauernhof, Chiffre
12345" is to walk the bulletin's index page for PDF links, download each
issue, and scan it page by page for the keyword vocabulary. What this
adapter emits is deliberately not a listing in the usual sense: it is a
*hit*, one per (page, matched term), carrying the page number and the
matched text so the claim is traceable back to "Gemeindeblatt X, KW 34, page
17" - not to a vague "somewhere in this PDF".

The PDF reading itself is ``hofradar.sources.adapters._pdfutil``'s, shared
with the Denkmalbörse exposé fetch and the paste box's upload; a missing
``pypdf`` surfaces here as a :class:`SourceDiscoveryError` because this
source cannot do anything at all without it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from hofradar.config import KeywordConfig, SearchProfile
from hofradar.contracts import RawListing
from hofradar.sources.adapters._pdfutil import (
    PdfError,
    PdfUnavailable,
    extract_pdf_text,
    find_pdf_links,
)
from hofradar.sources.base import SourceAdapter
from hofradar.sources.exceptions import SourceDiscoveryError

__all__ = ["PdfBulletinAdapter", "find_pdf_links", "scan_page_for_hits"]

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r"\d{1,2}\.\d{1,2}\.\d{2,4}")
_ISSUE_RE = re.compile(r"KW\s*\d{1,2}(?:\s*/\s*\d{2,4})?", re.IGNORECASE)
_CONTEXT_RADIUS = 80


def _context_snippet(text: str, index: int, term_len: int) -> str:
    start = max(index - _CONTEXT_RADIUS, 0)
    end = min(index + term_len + _CONTEXT_RADIUS, len(text))
    return text[start:end].strip()


def scan_page_for_hits(text: str, terms: list[str]) -> list[tuple[str, str]]:
    """Return (term, context_snippet) for every distinct vocabulary term found."""
    if not text:
        return []
    lowered = text.lower()
    hits: list[tuple[str, str]] = []
    for term in terms:
        idx = lowered.find(term.lower())
        if idx != -1:
            hits.append((term, _context_snippet(text, idx, len(term))))
    return hits


class PdfBulletinAdapter(SourceAdapter):
    """Walks ``options.bulletins`` (a list of index page URLs) for PDF issues."""

    #: A bulletin archive accumulates and never retracts. An advert missing from
    #: this week's issue is last week's advert, not a withdrawn one.
    enumerates = False

    async def discover(
        self, profile: SearchProfile, keywords: KeywordConfig
    ) -> AsyncIterator[RawListing]:
        bulletins: list[str] = list(self.options.get("bulletins") or [])
        if not bulletins:
            logger.info("%s: no bulletin index pages configured (options.bulletins)", self.key)
            return

        terms = keywords.all_terms
        if not terms:
            logger.info("%s: keyword vocabulary is empty - nothing to scan for", self.key)
            return

        any_readable = False

        for index_url in bulletins:
            try:
                response = await self.client.get(index_url)
                response.raise_for_status()
            except Exception as exc:  # noqa: BLE001 - one bad index page must not abort the run
                logger.warning("%s: could not fetch bulletin index %s: %s", self.key, index_url, exc)
                continue

            pdf_links = find_pdf_links(response.text, index_url)
            if not pdf_links:
                logger.info("%s: no PDF links found on %s", self.key, index_url)
                continue
            any_readable = True

            for pdf_url, link_text in pdf_links:
                try:
                    pdf_response = await self.client.get(pdf_url)
                    pdf_response.raise_for_status()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("%s: could not download %s: %s", self.key, pdf_url, exc)
                    continue

                try:
                    document = extract_pdf_text(pdf_response.content)
                except PdfUnavailable as exc:
                    raise SourceDiscoveryError(
                        "pdf_bulletin adapter requires the optional [pdf] extra: "
                        "pip install 'hofradar[pdf]'"
                    ) from exc
                except PdfError as exc:  # a corrupt/odd/oversized PDF must not stop the run
                    logger.warning("%s: could not read PDF %s: %s", self.key, pdf_url, exc)
                    continue

                date_match = _DATE_RE.search(link_text)
                document_date = date_match.group(0) if date_match else None
                issue_match = _ISSUE_RE.search(link_text)
                issue = issue_match.group(0) if issue_match else None

                for page_number, text in enumerate(document.pages, start=1):
                    for term, snippet in scan_page_for_hits(text, terms):
                        extra: dict[str, Any] = {
                            "page_number": page_number,
                            "matched_text": snippet,
                            "document_url": pdf_url,
                            "document_date": document_date,
                            "matched_term": term,
                            "is_bulletin_hit": True,
                        }
                        if issue:
                            extra["issue"] = issue
                        yield RawListing(
                            source_key=self.key,
                            url=f"{pdf_url}#page={page_number}",
                            title=f"{link_text} - Seite {page_number}: {term}",
                            description=snippet,
                            location_raw=self.region,
                            source_date_raw=document_date,
                            fetched_at=datetime.now(UTC),
                            extra=extra,
                        )

        if not any_readable:
            raise SourceDiscoveryError(
                f"{self.key}: none of {len(bulletins)} bulletin index page(s) yielded a PDF link"
            )

    async def fetch_detail(self, url: str) -> RawListing | None:
        # A hit already carries its page-level evidence; there is no separate
        # detail page to enrich it from.
        return None
