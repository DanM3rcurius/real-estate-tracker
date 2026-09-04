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


#: German postcodes run 01067-99998, so a leading "00" is never one. Requiring
#: five digits *and* a town-shaped word after them is what keeps this from
#: matching a price: "595.000" has separators, and "95000 EUR" fails the town
#: test below.
_POSTCODE_TOWN_RE = re.compile(
    r"\b(?!00)(\d{5})\s+"
    r"([A-ZÄÖÜ][a-zäöüß]+(?:[-\s][A-ZÄÖÜ][a-zäöüß]+)*)"
    r"(\s*[,(]?\s*(?:Landkreis|Lkr\.?|Kreis)\s+[A-ZÄÖÜ][a-zäöüß]+\)?)?"
)

#: Words that are shaped like a town but are plainly not one. The capitalised
#: word requires a lowercase tail, which already rules out EUR, VB and m2; this
#: catches the handful that would otherwise slip through.
_NOT_A_TOWN = frozenset(
    {"euro", "quadratmeter", "zimmer", "hektar", "kaufpreis", "baujahr", "wohnflaeche"}
)


def find_location_in_text(text: str | None) -> str | None:
    """Recover an unlabelled location from free text, or ``None``.

    Returns a fragment ``parse_location`` can read. Deliberately strict: a
    wrong town is far worse than no town, because it geocodes to a real place
    somewhere else and nothing downstream can tell that it is wrong. Anything
    this cannot identify with confidence is left for the caller to warn about.
    """
    if not text:
        return None
    for match in _POSTCODE_TOWN_RE.finditer(text):
        postcode, town, district = match.group(1), match.group(2), match.group(3)
        if town.split()[0].casefold() in _NOT_A_TOWN:
            continue
        return f"{postcode} {town}{district.rstrip() if district else ''}"
    return None
