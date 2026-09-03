"""German location-string parsing.

Portals format location differently: some give a full postal address, some
just a town, some a town qualified by its Landkreis (county) in parentheses,
some a vague "near town X" for a rural property with no street address at
all. ``parse_location`` extracts whatever structure is actually present
without inventing what isn't.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_DISTRICT_PAREN_RE = re.compile(
    r"\((?:landkreis|kreis|lkr\.?)\s+([^)]+)\)", re.IGNORECASE
)
#: "(Kreis)" with nothing further named means the town itself IS the
#: district seat / the reference to the county-level entity.
_DISTRICT_BARE_RE = re.compile(r"\((?:landkreis|kreis|lkr\.?)\)", re.IGNORECASE)
_POSTCODE_RE = re.compile(r"\b(\d{5})\b")
_BEI_RE = re.compile(r"\bbei\s+(.+)$", re.IGNORECASE)


@dataclass(slots=True)
class LocationParts:
    """The pieces ``parse_location`` could pull out of a free-text location."""

    street: str | None = None
    postcode: str | None = None
    town: str | None = None
    district: str | None = None


def _clean_town(fragment: str) -> str | None:
    fragment = fragment.split(",")[0].strip(" ,")
    return fragment or None


def parse_location(text: str | None) -> LocationParts:
    """Parse a free-text location string into its structural parts.

    Handles: "83620 Feldkirchen-Westerham" (postcode + town), "Vogtareuth
    (Landkreis Rosenheim)" (town + district), "Sacherl bei Bad Aibling" (a
    colloquial "near <town>" reference - the town after "bei" is taken as
    the actual location, since there is no street), "Rosenheim (Kreis),
    Bayern" (a bare county reference resolves district to the same name as
    the town), and "Musterstraße 12, 83024 Rosenheim" (a full street
    address). Any part that cannot be identified is left ``None`` rather
    than guessed.
    """
    if not text or not text.strip():
        return LocationParts()

    raw = text.strip()
    parts = LocationParts()

    district_match = _DISTRICT_PAREN_RE.search(raw)
    bare_district_match = None
    if district_match:
        parts.district = district_match.group(1).strip()
        remainder = raw[: district_match.start()] + raw[district_match.end() :]
    else:
        bare_district_match = _DISTRICT_BARE_RE.search(raw)
        remainder = raw
        if bare_district_match:
            remainder = raw[: bare_district_match.start()] + raw[bare_district_match.end() :]

    postcode_match = _POSTCODE_RE.search(remainder)
    if postcode_match:
        parts.postcode = postcode_match.group(1)
        remainder = remainder[: postcode_match.start()] + remainder[postcode_match.end() :]

    if "," in remainder:
        head, _, tail = remainder.partition(",")
        looks_like_street = bool(re.search(r"\d", head)) and bool(
            re.search(r"[A-Za-zÄÖÜäöüß]{3,}", head)
        )
        if looks_like_street:
            parts.street = head.strip()
            remainder = tail

    bei_match = _BEI_RE.search(remainder)
    parts.town = _clean_town(bei_match.group(1)) if bei_match else _clean_town(remainder)

    if bare_district_match and not parts.district:
        parts.district = parts.town

    return parts
