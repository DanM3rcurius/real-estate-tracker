"""Shared HTML -> RawListing lift used by every adapter that sees a full page.

This is deliberately *not* a general text normalizer - parsing raw strings
into typed values (price, area, dates, ...) is ``hofradar.normalize``'s job,
and this package must not import it. What lives here is the string-level
extraction every full-page adapter needs before that stage ever runs: find a
title, keep the visible text, read the og: metadata, collect image URLs, and
opportunistically pick up an obvious "Label: value" line (a pasted exposé and
a broker's detail page both tend to have one).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from hofradar.contracts import RawListing

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


def raw_listing_from_html(
    source_key: str,
    url: str,
    html: str,
    *,
    http_status: int | None = None,
    extra: dict[str, Any] | None = None,
) -> RawListing:
    """Turn one fetched HTML page into a RawListing.

    Title preference: og:title, then <title>, then the first <h1>.
    Description: the full visible body text (the whole point of "keep the
    full text" - later stages decide what in it matters).
    Images: og:image first, then every <img src> on the page, de-duplicated,
    resolved to absolute URLs against ``url``.
    """
    tree = HTMLParser(html)

    title = _meta_content(tree, "og:title")
    if not title:
        title_node = tree.css_first("title")
        if title_node is not None:
            text = title_node.text(strip=True)
            title = text or None
    if not title:
        h1 = tree.css_first("h1")
        if h1 is not None:
            text = h1.text(strip=True)
            title = text or None

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
        extra=extra or {},
        **labeled,
    )
