"""Small private helpers for the dedupe package.

Everything in here is deliberately local to ``hofradar.dedupe``: the text and
geo utilities that the rest of the system will eventually get from
``hofradar.normalize`` and ``hofradar.geo`` are re-implemented in miniature so
that this package can be developed, reasoned about and tested on its own. They
are intentionally dumb - the point of dedupe is the *evidence model*, not the
string handling.
"""

from __future__ import annotations

import math
import re

# TODO(integration): use hofradar.normalize.normalize_text
_UMLAUTS = {
    "ä": "ae",
    "ö": "oe",
    "ü": "ue",
    "ß": "ss",
    "à": "a",
    "á": "a",
    "â": "a",
    "è": "e",
    "é": "e",
    "ê": "e",
    "í": "i",
    "ó": "o",
    "ô": "o",
    "ú": "u",
    "ñ": "n",
    "ç": "c",
}
_UMLAUT_TABLE = str.maketrans(_UMLAUTS)
_NON_WORD = re.compile(r"[^0-9a-z]+")


def fold_text(text: str | None) -> str:
    """Casefold, fold umlauts, drop punctuation, squash whitespace.

    Used for every fuzzy text comparison so that "Hofstelle" and "HOFSTELLE"
    and "Hofstelle," are one token, and so that "Muenchen" == "München".
    """
    if not text:
        return ""
    folded = text.casefold().translate(_UMLAUT_TABLE)
    return _NON_WORD.sub(" ", folded).strip()


def slug(text: str | None) -> str:
    """Fold to a compact identifier suitable for a blocking key."""
    return fold_text(text).replace(" ", "-")


# TODO(integration): use hofradar.geo.haversine_km
_EARTH_RADIUS_M = 6_371_000.0


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Great-circle distance in metres between two (lat, lon) pairs."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(h)))


def phash_hamming(a: str | None, b: str | None) -> int | None:
    """Hamming distance between two perceptual-hash hex strings.

    Returns ``None`` when either side is missing or unparseable, which the
    caller must treat as "no information", never as "no match".
    """
    if not a or not b:
        return None
    a_clean = a.strip().lower()
    b_clean = b.strip().lower()
    if len(a_clean) != len(b_clean):
        return None
    try:
        return (int(a_clean, 16) ^ int(b_clean, 16)).bit_count()
    except ValueError:
        return None


def round_to(value: float | None, step: float) -> float | None:
    """Round ``value`` to the nearest multiple of ``step``."""
    if value is None:
        return None
    return round(value / step) * step


def relative_delta(a: float | None, b: float | None) -> float | None:
    """Symmetric relative difference of two positive quantities, or ``None``."""
    if a is None or b is None:
        return None
    base = max(abs(a), abs(b))
    if base == 0:
        return 0.0
    return abs(a - b) / base
