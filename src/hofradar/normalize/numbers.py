"""German-formatted numbers, prices, and areas.

German number formatting is the opposite of the US convention: "." groups
thousands, "," is the decimal separator ("750.000,50" = seven hundred fifty
thousand and fifty cents). Real listings additionally throw in currency
symbols, magnitude words ("Mio", "Tsd", "k"), and non-numeric price markers
("VB", "Preis auf Anfrage") that must be classified rather than parsed. This
module is the single place that understands all of that mess so every other
stage can work with typed floats.
"""

from __future__ import annotations

import re

from hofradar.db.enums import PriceType
from hofradar.normalize.text import normalize_text

# --------------------------------------------------------------------------- #
# Low-level number parsing
# --------------------------------------------------------------------------- #

#: A run of digits that may contain internal "." (thousands) or ","
#: (decimal) separators, anchored to start and end on a digit so a trailing
#: sentence-ending "." (e.g. "Baujahr 1995.") is never swallowed.
_NUMBER_TOKEN_RE = re.compile(r"\d[\d.,]*\d|\d")

_MAGNITUDE_WORDS: dict[str, float] = {
    "k": 1_000,
    "tsd": 1_000,
    "tausend": 1_000,
    "mio": 1_000_000,
    "million": 1_000_000,
    "millionen": 1_000_000,
}
#: Matches an optional magnitude word directly following a number token,
#: e.g. the " Mio" in "0,75 Mio" or the "k" in "750k".
_MAGNITUDE_RE = re.compile(
    r"\s*(k|tsd\.?|tausend|mio\.?|million(?:en)?)\b", re.IGNORECASE
)


def _parse_number_token(token: str) -> float:
    """Convert one already-matched digit run into a float.

    Disambiguation rule for "." (German real-estate text never uses "," as a
    thousands separator, so any "," is unambiguously the decimal point):
    if every group after a "." is exactly three digits and the leading group
    is at most three digits, "." is a thousands separator ("750.000" ->
    750000, "1.234.567" -> 1234567). Otherwise it is treated as a decimal
    point (covers stray non-German input like "0.75").
    """
    if "," in token:
        int_part, _, dec_part = token.rpartition(",")
        int_part = int_part.replace(".", "")
        return float(f"{int_part or '0'}.{dec_part}")
    if "." in token:
        groups = token.split(".")
        if len(groups[0]) <= 3 and all(len(g) == 3 for g in groups[1:]):
            return float("".join(groups))
        return float(token)
    return float(token)


def parse_german_number(text: str | None) -> float | None:
    """Parse the first German-formatted number found anywhere in ``text``.

    Understands an optional attached magnitude word ("k", "Tsd", "Mio" and
    spelled-out variants), so ``parse_german_number("750k") == 750000.0`` and
    ``parse_german_number("0,75 Mio") == 750000.0``. This is the low-level
    primitive ``parse_price`` and ``parse_area`` are built on; it does not
    know about currencies, units, or the price-ambiguity guard those two
    apply on top.

    Returns ``None`` if no number is found.
    """
    if not text:
        return None
    match = _NUMBER_TOKEN_RE.search(text)
    if not match:
        return None
    value = _parse_number_token(match.group())
    mag_match = _MAGNITUDE_RE.match(text[match.end() :])
    if mag_match:
        value *= _MAGNITUDE_WORDS[mag_match.group(1).lower().rstrip(".")]
    return value


# --------------------------------------------------------------------------- #
# Price parsing
# --------------------------------------------------------------------------- #

_CURRENCY_RE = re.compile(r"€|eur\b|euro\b", re.IGNORECASE)
#: "750 T€" - a magnitude marker fused onto the currency symbol rather than
#: the number, so it needs its own pattern ahead of the generic one.
_T_EURO_RE = re.compile(r"(\d[\d.,]*\d|\d)\s*t\s*€", re.IGNORECASE)


def _extract_price_value(text: str) -> float | None:
    """Extract a numeric price from ``text``, applying the ambiguity guard.

    Ambiguity guard (documented on :func:`parse_price`): a plain number is
    only accepted as a price if it carries a currency marker, a magnitude
    marker, or has at least 4 digits.
    """
    t_match = _T_EURO_RE.search(text)
    if t_match:
        return _parse_number_token(t_match.group(1)) * 1_000

    match = _NUMBER_TOKEN_RE.search(text)
    if not match:
        return None
    base = _parse_number_token(match.group())
    mag_match = _MAGNITUDE_RE.match(text[match.end() :])
    magnitude = _MAGNITUDE_WORDS[mag_match.group(1).lower().rstrip(".")] if mag_match else 1

    digit_count = sum(1 for ch in match.group() if ch.isdigit())
    has_currency = bool(_CURRENCY_RE.search(text))
    has_magnitude = magnitude != 1
    if not (has_currency or has_magnitude or digit_count >= 4):
        return None

    return base * magnitude


_AUCTION_MARKERS = ("verkehrswert", "mindestgebot")


def parse_price(text: str | None) -> tuple[float | None, str]:
    """Parse a German real-estate price string into ``(value_eur, price_type)``.

    ``price_type`` is always one of :class:`hofradar.db.enums.PriceType`'s
    values. Recognised markers: "Verkehrswert"/"Mindestgebot" -> AUCTION_MIN;
    "Preis auf Anfrage"/"auf Anfrage" -> ON_REQUEST; "VB"/"Verhandlungsbasis"/
    "VHB" -> NEGOTIABLE; a concrete number with no such marker -> ASKING.
    "Festpreis" ("fixed price") is the semantic opposite of negotiable and
    does not change the type away from ASKING.

    Ambiguity guard: a bare number such as "750" is NOT accepted as a price
    - real prices in this market never sit below EUR 1,000, so we require a
    currency marker (€/EUR), a magnitude marker (k/T€/Mio/Tsd), or at least
    4 digits before trusting a number as a price. A number failing the guard
    with no price-type keyword present returns ``(None, PriceType.UNKNOWN)``.
    """
    if text is None:
        return None, PriceType.UNKNOWN.value
    stripped = text.strip()
    if not stripped:
        return None, PriceType.UNKNOWN.value

    norm = normalize_text(stripped)
    price_type = PriceType.ASKING
    if any(marker in norm for marker in _AUCTION_MARKERS):
        price_type = PriceType.AUCTION_MIN
    elif "auf anfrage" in norm:
        price_type = PriceType.ON_REQUEST
    elif (
        re.search(r"\bvb\b", norm)
        or re.search(r"\bvhb\b", norm)
        or "verhandlungsbasis" in norm
    ):
        price_type = PriceType.NEGOTIABLE

    value = _extract_price_value(stripped)
    if value is None and price_type is PriceType.ASKING:
        price_type = PriceType.UNKNOWN
    return value, price_type.value


# --------------------------------------------------------------------------- #
# Area parsing
# --------------------------------------------------------------------------- #

#: (unit pattern, multiplier to square metres). Order matters only in that
#: each pattern must not falsely match another unit's text.
_AREA_UNITS: list[tuple[re.Pattern[str], float]] = [
    (re.compile(r"m\s*²|m2\b|quadratmeter", re.IGNORECASE), 1.0),
    (re.compile(r"hektar\b|\bha\b", re.IGNORECASE), 10_000.0),
    # Bavarian traditional land measure, ~3407 sqm (varies slightly by
    # region historically; 3407 is the commonly quoted modern conversion).
    (re.compile(r"tagwerk\b", re.IGNORECASE), 3_407.0),
    (re.compile(r"\bqm\b", re.IGNORECASE), 1.0),
]


def parse_area(text: str | None) -> float | None:
    """Parse a German area string into square metres, always as a float.

    Handles "5.000 m²", "5000 qm", "5000 m2", "5.000 Quadratmeter", hectares
    ("0,5 ha", "1 Hektar" - multiplied by 10,000), and the Bavarian "Tagwerk"
    (~3407 sqm). A "ca." (circa) prefix is ignored automatically since it
    contains no digits. When a number is found but no unit can be identified
    anywhere in the text, the number is returned as-is on the assumption
    that the field it came from (e.g. ``RawListing.land_raw``) already
    implies square metres - this is a deliberate default, not a guess at an
    unknown unit.
    """
    if not text or not text.strip():
        return None
    match = _NUMBER_TOKEN_RE.search(text)
    if not match:
        return None
    base = _parse_number_token(match.group())

    tail = text[match.end() : match.end() + 40]
    for pattern, multiplier in _AREA_UNITS:
        if pattern.search(tail):
            return base * multiplier
    for pattern, multiplier in _AREA_UNITS:
        if pattern.search(text):
            return base * multiplier
    return base


def prices_equivalent(a: float | None, b: float | None, tol_pct: float = 1.0) -> bool:
    """True when two prices differ by no more than ``tol_pct`` percent.

    Two independently-scraped or -formatted representations of the *same*
    price ("750k" vs. "750.000 EUR") should compare equal outright; two
    genuinely different prices that merely happen to be close ("750.000"
    vs. "749.900", a common negotiated-down re-listing) must NOT be silently
    merged by the parser itself - ``parse_price`` returns the exact value it
    read. This helper is the explicit, opt-in tolerance check for callers
    (e.g. change detection) that want to treat "close enough" as unchanged.
    """
    if a is None or b is None:
        return a is None and b is None
    if a == b:
        return True
    baseline = max(abs(a), abs(b))
    if baseline == 0:
        return True
    return abs(a - b) / baseline * 100.0 <= tol_pct
