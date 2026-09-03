"""OVB's regional property portal for the Rosenheim / Chiemgau / Inn-Salzach area.

Why a newspaper portal rather than a national one: OVB aggregates the regional
brokers *and* the classified ads from its own daily papers, including private
sellers with Chiffre references. That second inventory is the reason this
source exists - it is the closest thing to the Gemeindeblatt small ads that
reaches a machine-readable page, and the existing hidden_score vocabulary
already recognises every signal it carries (chiffre, privatverkauf,
kein_makler, preis_auf_anfrage).

Discovery walks one faceted search URL per configured municipality/property
type rather than crawling: the site exposes /kaufen/{typ}/{ort} directly, so
there is no reason to ask for anything we do not want. Detail pages carry a
stable six-character identifier that survives a broker rewriting the
headline, which is what makes deduplication reliable here - the title slug is
cosmetic.

Pagination is the load-bearing part of this adapter. A single search page
carries ~20 of what can be 180+ results (see the real capture at
tests/fixtures/html/ovbimmo_search_rosenheim.html: 20 links, "186
Ergebnisse", <link rel="next">). Per invariant 4b, a capped or truncated walk
proves nothing by its silence, so `discover()` follows <link rel="next"> up
to a configurable page cap and calls `mark_enumeration_incomplete` the moment
it stops before that trail runs out - whether because the cap was hit, a page
errored, a fetch raised, a listed detail page could not be read, the caller
stopped draining the generator, or the whole run parsed no detail links at
all. Only a walk that reaches a page with no further `rel="next"` link, having
actually seen something, may leave the enumeration flagged complete.

Adverts run for a fixed paid window, so the registry sets listing_ttl_days and
the lifecycle reads a disappearance after that window as EXPIRED rather than
REMOVED. See docs/DECISIONS.md entry 15.

A real detail page (tests/fixtures/html/ovbimmo_detail.html) confirms the
structure the plan expected: a `dataLayer` JSON blob carrying `listing_id`
(the same 6-char code), `property_price` in cents, `rooms`, `area`,
`postal_code`, `locality`, `geo_hierarchy_*`; and the headline figures
(price/rooms/area) rendered as three `eps-item` blocks, e.g. `<div
class="eps-item eps-item-price col-4">690.000,00 €<br> <span
class="eps-item-unit">Kaufpreis</span></div>` - the VALUE first, then the
label in a nested span. (The page also has `col-label`/`col-value` divs, but
those are unrelated sidebar widgets - Umzugsrechner, Immobilienwert, Kredit -
not the Objektdaten figures.)

Exactly two of those structured fields are read here, and only because
nothing else on the page carries them: `postal_code` and `locality`, lifted
verbatim from the `dataLayer` by `_datalayer_location`. Without them an OVB
property has no town, no postcode, no geocode query and therefore no
`distance_air_km` at all - the report's in-radius yield and its per-Gemeinde
coverage map are then structurally unable to see this source, which is a
worse failure than the vendor coupling. Everything else in that blob stays
unread: the figures are numbers (`property_price` is in **cents**), and an
adapter that converts anything has crossed into `hofradar.normalize`'s job.
For those, `fetch_detail` still uses only the generic HTML fallback in
`_htmlutil` (og:title/description/images), which is markup-agnostic on
purpose. Concretely, on the real capture: title and images come through
cleanly via og: tags, but `extract_labeled_fields` (which wants a single
"Label: value" line) gets nothing, because "690.000,00 €" and "Kaufpreis"
land on separate lines in that order, never as "Kaufpreis: 690.000,00 €".
Parsing the `eps-item` blocks would need a page-specific extension to this
adapter (or to `_htmlutil`) that does not exist yet - the price/rooms/area
figures still reach `description` as plain text, so the hidden_score keyword
vocabulary still fires on them, but no typed RawListing field does. See
docs/SOURCES.md.

The external id always comes from the URL, never the page - deliberately,
since it needs no parser at all and survives a broker rewriting the title.
`fetch_detail` does NOT guess `contact_kind` the way the Denkmalbörse adapter
does - OVB carries both broker and private inventory (the one real detail
page captured so far is a broker listing with "Provision für Käufer"), and
guessing wrong would corrupt the hidden_score signal this source exists to
feed. The same capture is independent evidence for never trusting the
`dataLayer` for this either: its `features` list says
`free_of_commission`, while the page body plainly says "Provision für
Käufer" - the JSON blob and the human-facing text disagree on commission,
so `contact_kind` stays unset rather than derived from either.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from hofradar.contracts import RawListing
from hofradar.sources.adapters._htmlutil import raw_listing_from_html
from hofradar.sources.base import SourceAdapter

if TYPE_CHECKING:  # pragma: no cover
    from hofradar.config import KeywordConfig, SearchProfile

logger = logging.getLogger(__name__)

#: Faceted search: /kaufen/{typ}/{ort}. "haus" and "grundstueck" exclude the
#: Eigentumswohnungen that dominate the portal and are never what we want.
SEARCH_TEMPLATE = "/kaufen/{property_type}/{municipality}"
DEFAULT_PROPERTY_TYPES: tuple[str, ...] = ("haus", "grundstueck")
DEFAULT_BASE_URL = "https://ovbimmo.de"

#: /immobilien/<cosmetic-slug>-GZJFXJ - the trailing code is the identity. The
#: slug changes whenever a broker rewrites the headline; the code does not.
DETAIL_HREF_RE = re.compile(r"/immobilien/[a-z0-9-]+-([A-Z0-9]{6})(?:[/?#]|$)")

#: Rendered by the theme as <link rel="next" href="...?page=N" /> in <head>.
NEXT_LINK_SELECTOR = 'link[rel="next"]'

#: How many result pages `discover()` walks per municipality/property-type
#: facet before it gives up and calls `mark_enumeration_incomplete`. A real
#: capture (see tests/fixtures/html/ovbimmo_search_rosenheim.html) showed 186
#: results / ~20 per page = 10 pages for one Landkreis-wide facet; the
#: default below is deliberately smaller so a misconfigured facet cannot turn
#: into an unbounded crawl - override per source via
#: options.max_pages_per_search when a facet genuinely needs more.
DEFAULT_MAX_PAGES_PER_SEARCH = 5

#: The theme opens its analytics blob with ``dataLayer = [ ... ];`` inside an
#: inline <script>. Matched only up to the ``[`` - the array is then handed to
#: a real JSON decoder, because the blob contains nested ``]`` characters
#: ("organizations":[{"id":"2332"}]) that any non-greedy regex would stop at.
_DATALAYER_ASSIGNMENT_RE = re.compile(r"\bdataLayer\s*=\s*(?=\[)")

#: dataLayer key -> the RawListing field it fills. Deliberately only the two
#: address strings. Everything else in that blob is either a number
#: (``property_price`` in **cents**, ``rooms``, ``area``) - which an adapter
#: may not convert, because turning a string into a typed value is
#: ``hofradar.normalize``'s job - or a claim the page's own body text
#: contradicts (``features`` says free_of_commission while the exposé says
#: "Provision für Käufer"). Both of these arrive as strings and are handed on
#: verbatim.
_DATALAYER_FIELD_MAP: dict[str, str] = {
    "postal_code": "postcode",
    "locality": "town",
}


def _datalayer_location(html: str) -> dict[str, str]:
    """The address strings from the page's ``dataLayer``, or ``{}``.

    Why read a vendor's own analytics blob at all: nothing else on an OVB
    detail page states the town or the postcode in a machine-readable place.
    The visible exposé mentions the town only inside prose, and the generic
    og:/labelled-field fallback consequently produces no location whatsoever -
    which leaves every OVB property with ``town = NULL`` and no geocode query,
    so ``distance_air_km`` is never computed and the report's in-radius yield
    and per-Gemeinde coverage are both structurally blind to this source.
    Reading a site-specific structured field is exactly what a *dedicated*
    adapter is for; the generic adapters must stay generic, which is why the
    equivalent for the Atom route is configuration rather than code.

    Never raises: a template change, a moved blob or malformed JSON leaves the
    caller with no location rather than an exception, the same way every other
    optional field on this page behaves.
    """
    match = _DATALAYER_ASSIGNMENT_RE.search(html)
    if match is None:
        return {}
    try:
        payload, _ = json.JSONDecoder().raw_decode(html, match.end())
    except ValueError:
        logger.debug("ovbimmo: dataLayer present but did not decode as JSON")
        return {}
    entries: list[Any] = payload if isinstance(payload, list) else [payload]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        found = {
            field: value.strip()
            for key, field in _DATALAYER_FIELD_MAP.items()
            # Strings only: a number here would have to be converted to be
            # useful, and an adapter converting anything is the boundary this
            # module must not cross.
            if isinstance(value := entry.get(key), str) and value.strip()
        }
        if found:
            return found
    return {}


class OvbimmoAdapter(SourceAdapter):
    """Fetches faceted search result pages and each listing's detail page.

    Parsing stays at the string level throughout - see `fetch_detail`. Turning
    "Preis auf Anfrage" into a typed price, or a description into keyword
    hits, is `hofradar.normalize`'s job, not this adapter's.
    """

    key = "ovbimmo"

    async def discover(
        self, profile: SearchProfile, keywords: KeywordConfig
    ) -> AsyncIterator[RawListing]:
        self.begin_enumeration()
        base = self.base_url or DEFAULT_BASE_URL
        municipalities: list[str] = list(self.options.get("municipalities") or [])
        property_types: tuple[str, ...] = tuple(
            self.options.get("property_types") or DEFAULT_PROPERTY_TYPES
        )
        max_pages = int(self.options.get("max_pages_per_search", DEFAULT_MAX_PAGES_PER_SEARCH))

        if not municipalities:
            logger.warning("%s: no municipalities configured; discovering nothing", self.key)
            self.mark_enumeration_incomplete("no municipalities configured; nothing was searched")
            return

        seen: set[str] = set()
        facets_walked_fully = False
        try:
            for municipality in municipalities:
                for property_type in property_types:
                    path = SEARCH_TEMPLATE.format(
                        property_type=property_type, municipality=municipality
                    )
                    async for listing in self._walk_search(
                        urljoin(base, path), base, max_pages, seen, municipality, property_type
                    ):
                        yield listing
            facets_walked_fully = True
        finally:
            # Invariant 4b, the same two checks denkmalboerse.discover() makes
            # and for the same reasons - the rule is a property of the project,
            # not of one adapter, so the two must not disagree about it.
            #
            # A consumer that stops draining early tears this generator down
            # via GeneratorExit at the last yield; the ``finally`` is what
            # still runs the check in that case rather than leaving the True
            # that begin_enumeration() set.
            #
            # The zero-row check is the OVB analogue of denkmalboerse's
            # ``object_count == 0``: a search response that parses to no
            # detail links at all is a template change, not an empty market,
            # and a TTL source is exactly where that fiction does the most
            # damage (every still-visible row would be swept to EXPIRED). It
            # is deliberately whole-run rather than per-page: one faceted
            # /kaufen/{typ}/{ort} URL genuinely can have no houses for sale
            # this week, and the last page of a walk that ended cleanly is
            # normally empty by construction - but every facet on the site
            # renders from one template, so a template change takes all of
            # them out together and leaves ``seen`` empty.
            if not facets_walked_fully:
                self.mark_enumeration_incomplete(
                    "discover() was not fully consumed - only "
                    f"{len(seen)} listing(s) were examined"
                )
            elif not seen:
                self.mark_enumeration_incomplete(
                    "parsed zero listing links from every configured search facet - "
                    "possibly a template change"
                )

    async def _walk_search(
        self,
        start_url: str,
        base: str,
        max_pages: int,
        seen: set[str],
        municipality: str,
        property_type: str,
    ) -> AsyncIterator[RawListing]:
        """Follow <link rel="next"> from `start_url` up to `max_pages` pages."""
        facet = f"{property_type}/{municipality}"
        url: str | None = start_url
        pages_fetched = 0

        while url is not None:
            if pages_fetched >= max_pages:
                self.mark_enumeration_incomplete(
                    f"{facet}: stopped after {max_pages} page(s); more results "
                    f"remained at {url}"
                )
                return

            try:
                response = await self.client.get(url)
            except Exception as exc:  # noqa: BLE001 - one bad search page must not abort the run
                logger.warning("%s: search fetch failed for %s: %s", self.key, url, exc)
                self.mark_enumeration_incomplete(f"{facet}: fetch of {url} failed: {exc}")
                return
            pages_fetched += 1

            if response.status_code >= 400:
                self.mark_enumeration_incomplete(
                    f"{facet}: page {pages_fetched} ({url}) returned {response.status_code}"
                )
                return

            tree = HTMLParser(response.text)
            for node in tree.css("a"):
                href = node.attributes.get("href") or ""
                match = DETAIL_HREF_RE.search(href)
                if match is None or match.group(1) in seen:
                    continue
                seen.add(match.group(1))
                detail_url = urljoin(base, href)
                listing = await self.fetch_detail(detail_url)
                if listing is None:
                    # The search page listed this id; fetch_detail returning
                    # None (a 4xx/5xx or a transport failure) means we never
                    # actually looked at it - its absence from the observed
                    # set must not be read as "gone". Without this, a single
                    # flaky detail fetch shrinks the observed set silently
                    # and can turn into a false REMOVED via mark_missing.
                    self.mark_enumeration_incomplete(
                        f"{facet}: detail fetch failed for listed id "
                        f"{match.group(1)} ({detail_url})"
                    )
                    continue
                yield listing

            next_node = tree.css_first(NEXT_LINK_SELECTOR)
            next_href = next_node.attributes.get("href") if next_node is not None else None
            url = urljoin(base, next_href) if next_href else None

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
        # The external id lives in the URL, not the page - deliberate, since
        # it needs no parser at all and survives a broker rewriting the
        # title (see the module docstring).
        match = DETAIL_HREF_RE.search(url)
        if match is not None:
            listing.external_id = match.group(1)
        for field, value in _datalayer_location(response.text).items():
            setattr(listing, field, value)
        return listing
