"""Shared HTML -> RawListing lift used by every adapter that sees a full page.

This is deliberately *not* a general text normalizer - parsing raw strings
into typed values (price, area, dates, ...) is ``hofradar.normalize``'s job,
and this package must not import it. What lives here is the string-level
extraction every full-page adapter needs before that stage ever runs: find a
title, keep the visible text, read the og: metadata, collect image URLs, and
opportunistically pick up an obvious "Label: value" line (a pasted exposé and
a broker's detail page both tend to have one).

It also answers the question nothing used to ask: *what kind of page is this?*
A crawler reaches a portal's result list, its bookmark widget and its login
form on exactly the same code path as a real advert, and a fact-count gate
cannot tell them apart - fed a search page, ``extract_labeled_fields`` below
happily returns a price from one result card and a living area from another
(GitHub issue #10). So :func:`page_kind` reads the *shape* of the page, and
:func:`listing_title` prefers the page's own headline over the portal chrome
that ``og:title`` and ``<title>`` so often carry.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

from selectolax.parser import HTMLParser

from hofradar.contracts import (
    PAGE_KIND_INDEX,
    PAGE_KIND_LISTING,
    PAGE_KIND_UTILITY,
    PageKind,
    RawListing,
)

#: "Label: value" lines mapped onto the RawListing field they most likely mean.
#: Best-effort only - this captures the raw substring, it never parses it.
_LABEL_FIELD_MAP: dict[str, str] = {
    "kaufpreis": "price_raw",
    "preis": "price_raw",
    "verkaufspreis": "price_raw",
    "kaufpreisvorstellung": "price_raw",
    "preisvorstellung": "price_raw",
    "price": "price_raw",
    "grundstück": "land_raw",
    "grundstueck": "land_raw",
    "grundstücksfläche": "land_raw",
    "grundstuecksflaeche": "land_raw",
    "grundstücksgröße": "land_raw",
    "grundstuecksgroesse": "land_raw",
    "grundstuecksgroesse (m2)": "land_raw",
    "land": "land_raw",
    "wohnfläche": "living_raw",
    "wohnflaeche": "living_raw",
    "wohnfl.": "living_raw",
    "living": "living_raw",
    "nutzfläche": "usable_raw",
    "nutzflaeche": "usable_raw",
    "usable": "usable_raw",
    "zimmer": "rooms_raw",
    "zimmeranzahl": "rooms_raw",
    "rooms": "rooms_raw",
    "baujahr": "year_raw",
    "year": "year_raw",
    "ort": "location_raw",
    "lage": "location_raw",
    "standort": "location_raw",
    "adresse": "location_raw",
    "address": "location_raw",
}


#: A trailing parenthetical qualifier on a label, e.g. "Wohnfläche (Bauernhaus)"
#: or "Nutzfläche (Wirtschaftsteil)" - owner-written exposés for a Hofstelle
#: routinely split an area figure this way between the living quarters and the
#: working/farm part of the building. The qualifier is real content, not
#: noise, so it is never stripped from the label text itself - it is only
#: bypassed when the plain label alone would otherwise fail to match below.
_TRAILING_PARENTHETICAL_RE = re.compile(r"\s*\([^)]*\)\s*$")


def extract_labeled_fields(text: str) -> dict[str, str]:
    """Scan "Label: value" lines for the fields exposés almost always spell out.

    A label with a trailing parenthetical qualifier matches the same field as
    its unqualified form *only when that unqualified form is already a known
    key* - this fills in the common "which part of the building" qualifier
    without turning the lookup into a fuzzy match for labels this map has
    never heard of in any form.
    """
    found: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        label, _, value = line.partition(":")
        key = label.strip().lower().rstrip(".")
        value = value.strip()
        if not value:
            continue
        field = _LABEL_FIELD_MAP.get(key)
        if field is None:
            base_key = _TRAILING_PARENTHETICAL_RE.sub("", key).strip()
            if base_key != key:
                field = _LABEL_FIELD_MAP.get(base_key)
        if field and field not in found:
            found[field] = value
    return found


def _meta_content(tree: HTMLParser, prop: str) -> str | None:
    node = tree.css_first(f'meta[property="{prop}"]') or tree.css_first(f'meta[name="{prop}"]')
    if node is None:
        return None
    content = node.attributes.get("content")
    return content.strip() if content else None


def _node_text(tree: HTMLParser, selector: str) -> str | None:
    node = tree.css_first(selector)
    if node is None:
        return None
    return node.text(strip=True) or None


# --------------------------------------------------------------------------- #
# Is this page a listing at all?
# --------------------------------------------------------------------------- #

#: Path segments that name a portal's own machinery rather than an advert.
#: Matched as whole segments (with an optional file extension) so that
#: /suchergebnisse is not read as /suche and a "Loginhof" slug is not read as
#: a login form.
_UTILITY_SEGMENTS: tuple[str, ...] = (
    "merkliste",
    "merkzettel",
    "favoriten",
    "suchagent",
    "login",
    "logout",
    "anmelden",
    "abmelden",
    "register",
    "registrieren",
    "impressum",
    "datenschutz",
    "agb",
    "kontakt",
    "suche",
    "search",
)

UTILITY_PATH_RE = re.compile(
    r"(?:^|/)(?:" + "|".join(_UTILITY_SEGMENTS) + r")(?:\.[A-Za-z0-9]+)?(?:/|$)",
    re.IGNORECASE,
)

#: The same vocabulary as a page's own headline, for the case the URL cannot
#: help: a human pastes the markup of a bookmark list and the paste box has no
#: URL to attribute it to (``manual:<timestamp>``) - which is precisely how the
#: "Merkliste" property in issue #10 was created.
_UTILITY_HEADINGS: frozenset[str] = frozenset(
    {
        "merkliste",
        "merkzettel",
        "favoriten",
        "suchagent",
        "login",
        "anmelden",
        "abmelden",
        "registrieren",
        "registrierung",
        "impressum",
        "datenschutz",
        "datenschutzerklarung",
        "agb",
        "kontakt",
    }
)

#: schema.org types that say "this page IS one thing on offer".
_LISTING_LD_TYPES: frozenset[str] = frozenset(
    {
        "realestatelisting",
        "product",
        "offer",
        "singlefamilyresidence",
        "house",
        "apartment",
        "residence",
        "accommodation",
    }
)

#: schema.org types that say "this page is a list of them".
_INDEX_LD_TYPES: frozenset[str] = frozenset(
    {"searchresultspage", "collectionpage", "itemlist"}
)

#: Keys under which a JSON-LD document nests further objects that describe
#: *this page*. ``itemListElement`` is deliberately absent: a result list's
#: entries are typed as the things they link to (Product, Offer, ...), so
#: walking into them makes every search page look like a listing - which is
#: exactly the mistake issue #10 is about, one layer down.
_LD_NESTED_KEYS: tuple[str, ...] = ("@graph", "mainEntity", "mainEntityOfPage")

#: How deep the JSON-LD walk goes before giving up on a pathological document.
_MAX_LD_DEPTH = 6

#: How many sibling links of one URL shape make a page a result list rather
#: than one advert. Measured on the real captures in tests/fixtures/html:
#: the ovbimmo search page has 20 links under ``/immobilien/*``, while its
#: detail page's largest such group (the related-ads rail) has 5.
_INDEX_MIN_SIBLING_LINKS = 10

#: A link one segment deep is site navigation ("/kaufen", "/impressum"), never
#: a result card, so those never count towards the group size above.
_MIN_CARD_PATH_SEGMENTS = 2

#: Separators a portal puts between a headline and its own name.
_TITLE_SUFFIX_RE = re.compile(r"^(?P<head>.+?)\s*[|–—·-]\s*(?P<tail>[^|–—·-]+)$")

#: A suffix longer than this is prose, not a site name, and is left alone.
_MAX_SITE_SUFFIX_LEN = 40

#: What has to survive the strip. Shorter than this and the "site name" was
#: probably the headline itself.
_MIN_STRIPPED_TITLE_LEN = 8


def is_utility_url(url: str) -> bool:
    """Does this URL name a portal function (bookmarks, login, imprint, ...)?"""
    return bool(UTILITY_PATH_RE.search(urlsplit(url).path))


def _ld_documents(tree: HTMLParser) -> Iterator[dict[str, Any]]:
    """Every JSON-LD object on the page, nested ones included.

    Never raises: a hand-written or truncated blob is a reason to learn
    nothing from it, not a reason to fail the fetch.
    """
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            payload = json.loads(node.text())
        except ValueError:
            continue
        yield from _walk_ld(payload, depth=0)


def _walk_ld(payload: Any, *, depth: int) -> Iterator[dict[str, Any]]:
    if depth > _MAX_LD_DEPTH:
        return
    if isinstance(payload, list):
        for item in payload:
            yield from _walk_ld(item, depth=depth + 1)
        return
    if not isinstance(payload, dict):
        return
    yield payload
    for key in _LD_NESTED_KEYS:
        if key in payload:
            yield from _walk_ld(payload[key], depth=depth + 1)


def _ld_types(document: dict[str, Any]) -> set[str]:
    raw = document.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    return {value.casefold() for value in values if isinstance(value, str)}


#: Umlaut folding, so "Datenschutzerklärung" and a site name written either
#: way compare equal. Only ever used for comparison, never for display.
_UMLAUT_FOLDING = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"})


def _fold(text: str) -> str:
    """Casefolded, umlaut-folded, punctuation-free - for comparing names only."""
    return re.sub(r"[^a-z0-9]", "", text.casefold().translate(_UMLAUT_FOLDING))


def _looks_like_a_utility_heading(tree: HTMLParser) -> bool:
    for text in (_node_text(tree, "h1"), _node_text(tree, "title")):
        if text is None:
            continue
        head = _TITLE_SUFFIX_RE.match(text)
        candidate = head.group("head") if head is not None else text
        if _fold(candidate) in _UTILITY_HEADINGS:
            return True
    return False


def _largest_sibling_link_group(tree: HTMLParser) -> int:
    """How many links share one URL shape - a result list's fingerprint.

    Grouped by everything but the last path segment, which is what makes
    twenty ``/immobilien/<slug>-<id>`` cards one group and leaves a detail
    page's handful of navigation links scattered across many.
    """
    groups: dict[str, set[str]] = {}
    for node in tree.css("a[href]"):
        href = node.attributes.get("href") or ""
        path = urlsplit(href).path
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) < _MIN_CARD_PATH_SEGMENTS:
            continue
        groups.setdefault("/".join(segments[:-1]), set()).add(path)
    return max((len(paths) for paths in groups.values()), default=0)


def page_kind(tree: HTMLParser, url: str) -> PageKind:
    """Classify one fetched page as a listing, an index of listings, or neither.

    Why shape rather than substance: the tempting gate is "no price, no area,
    no location - drop it", and it does not work. ``extract_labeled_fields``
    scans the whole page, so a portal's search results hand it a complete set
    of plausible facts assembled from several different result cards - the
    chimera in issue #10 had a property type from one advert, a land area
    from a second and a town from a third. A page that lists twenty properties
    is not a poorly-described property; it is a different kind of page, and
    that is the thing to test for.

    The signals, in order of how much they prove: the URL names a portal
    function; the page's own headline is one ("Merkliste"); the page says what
    it is in JSON-LD; the page is built out of many sibling result cards.
    Anything else is treated as a listing, because a small broker's detail
    page states nothing about itself at all and refusing those would be far
    worse than the bug being fixed.
    """
    if is_utility_url(url):
        return PAGE_KIND_UTILITY
    if _looks_like_a_utility_heading(tree):
        return PAGE_KIND_UTILITY

    declared: set[str] = set()
    for document in _ld_documents(tree):
        declared |= _ld_types(document)
    # An index declaration outranks a listing one, because a portal makes both
    # at once: the real ovbimmo search capture declares SearchResultsPage AND
    # a Product named after the page itself, whose "offers" is an
    # AggregateOffer over all 186 results. The page saying it is a result list
    # is a statement about the whole page; the Product beside it is not.
    if declared & _INDEX_LD_TYPES:
        return PAGE_KIND_INDEX
    if declared & _LISTING_LD_TYPES:
        return PAGE_KIND_LISTING

    if _largest_sibling_link_group(tree) >= _INDEX_MIN_SIBLING_LINKS:
        return PAGE_KIND_INDEX
    return PAGE_KIND_LISTING


# --------------------------------------------------------------------------- #
# The headline, as opposed to the portal's own name for the page
# --------------------------------------------------------------------------- #


def _ld_listing_name(tree: HTMLParser) -> str | None:
    for document in _ld_documents(tree):
        if not _ld_types(document) & _LISTING_LD_TYPES:
            continue
        name = document.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


def _is_site_name(tail: str, *, site_name: str | None, host: str) -> bool:
    """Is this trailing fragment the site's own name rather than content?

    Deliberately narrow: it has to match what the page itself says its site is
    called (``og:site_name``) or the host it was fetched from. Everything
    after a dash is otherwise routinely part of a German headline
    ("Hofstelle mit Stadel - 8.000 m² Grund"), and losing that is a worse bug
    than keeping a site name.
    """
    folded = _fold(tail)
    if not folded:
        return False
    if site_name and folded == _fold(site_name):
        return True
    folded_host = _fold(host.removeprefix("www."))
    return bool(folded_host) and folded in folded_host


def _strip_site_suffix(title: str, *, site_name: str | None, host: str) -> str:
    match = _TITLE_SUFFIX_RE.match(title)
    if match is None:
        return title
    head, tail = match.group("head").strip(), match.group("tail").strip()
    if len(tail) > _MAX_SITE_SUFFIX_LEN or len(head) < _MIN_STRIPPED_TITLE_LEN:
        return title
    if not _is_site_name(tail, site_name=site_name, host=host):
        return title
    return head


def listing_title(tree: HTMLParser, url: str) -> str | None:
    """The headline of the thing on offer, with the portal's chrome removed.

    ``og:title`` is written for the social-media card, and on a portal that is
    the page's marketing name ("Immobilien in Rosenheim (Kreis) kaufen -
    OVBimmo.de"), not the property's. Preferring it over the ``<h1>`` is how
    "Merkliste" became a property's canonical title (issue #10). So: what the
    page declares in JSON-LD, then its own headline, and only then the two
    metadata fields that describe the *page*.
    """
    site_name = _meta_content(tree, "og:site_name")
    host = urlsplit(url).hostname or ""
    for candidate in (
        _ld_listing_name(tree),
        _node_text(tree, "h1"),
        _meta_content(tree, "og:title"),
        _node_text(tree, "title"),
    ):
        if candidate:
            return _strip_site_suffix(candidate, site_name=site_name, host=host)
    return None


def raw_listing_from_html(
    source_key: str,
    url: str,
    html: str,
    *,
    http_status: int | None = None,
    extra: dict[str, Any] | None = None,
) -> RawListing:
    """Turn one fetched HTML page into a RawListing.

    Title: :func:`listing_title` - the headline, not the portal's page name.
    Page kind: :func:`page_kind` - whether this was a listing at all. The
    fields below are extracted either way, because the page's own facts are
    what a human needs to see when the refusal is explained to them.
    Description: the full visible body text (the whole point of "keep the
    full text" - later stages decide what in it matters).
    Images: og:image first, then every <img src> on the page, de-duplicated,
    resolved to absolute URLs against ``url``.
    """
    tree = HTMLParser(html)

    title = listing_title(tree, url)

    body_node = tree.body or tree.root
    description = body_node.text(separator="\n", strip=True) if body_node is not None else html
    if not description:
        description = _meta_content(tree, "og:description")

    image_urls: list[str] = []
    og_image = _meta_content(tree, "og:image")
    if og_image:
        image_urls.append(urljoin(url, og_image))
    for img in tree.css("img[src]"):
        src = img.attributes.get("src")
        if not src:
            continue
        resolved = urljoin(url, src)
        if resolved not in image_urls:
            image_urls.append(resolved)

    labeled = extract_labeled_fields(description or "")

    return RawListing(
        source_key=source_key,
        url=url,
        title=title,
        description=description,
        image_urls=image_urls,
        http_status=http_status,
        fetched_at=datetime.now(UTC),
        page_kind=page_kind(tree, url),
        extra=extra or {},
        **labeled,
    )
