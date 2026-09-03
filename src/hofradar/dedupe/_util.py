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
from datetime import UTC, datetime
from urllib.parse import parse_qsl, urlsplit, urlunsplit

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


#: Query parameters that only ever describe *how a visitor arrived*, never
#: which listing they arrived at. Restricted to the ``utm_`` family and a
#: fixed set of advertising click identifiers, all of which are unambiguous.
#: Deliberately NOT included: ``ref``, ``source``, ``id``, ``page`` and
#: friends - plenty of sites use those to select content, and dropping one
#: would collide two genuinely different listings, which is a far worse error
#: than failing to join two spellings of one.
_TRACKING_PARAM_PREFIXES: tuple[str, ...] = ("utm_",)
_TRACKING_PARAMS: frozenset[str] = frozenset(
    {"gclid", "dclid", "fbclid", "msclkid", "igshid", "mc_cid", "mc_eid", "yclid", "twclid"}
)

#: Ports that carry no information because the scheme already implies them.
_DEFAULT_PORTS: dict[str, str] = {"http": "80", "https": "443"}

#: http and https reach the same document, so the key folds them together.
#: Any other scheme is kept as written rather than guessed at.
_WEB_SCHEMES: frozenset[str] = frozenset({"http", "https"})
_CANONICAL_WEB_SCHEME = "https"


def _is_tracking_param(name: str) -> bool:
    lowered = name.lower()
    return lowered in _TRACKING_PARAMS or lowered.startswith(_TRACKING_PARAM_PREFIXES)


def canonical_url(url: str | None) -> str | None:
    """The identity of a listing page, with the cosmetic parts removed.

    A URL identifies a listing, so two sources that publish the same URL are
    publishing the same listing - see ``compare_facts``, which treats that as
    proof across sources. This function decides what "the same URL" means, and
    its job is to be *conservative*: it removes only differences that provably
    cannot select different content, because a false join fuses two farmsteads
    into one property and destroys both their histories, while a missed join
    merely leaves a duplicate the ordinary evidence model can still catch.

    Removed: the fragment (never sent to a server), a default port, a ``www.``
    host prefix, userinfo, a trailing slash, the http/https distinction, and
    the tracking parameters named above. Query parameters are otherwise kept
    (sorted, so ``?a=1&b=2`` and ``?b=2&a=1`` agree) and the path's case is
    kept, because paths can be case-sensitive and a query parameter this
    function does not recognise may well be what selects the listing.

    Returns ``None`` for anything without a host - a bare path or a pasted
    fragment of text is not an identity, and must never match another one.
    """
    if not url or not url.strip():
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if not host:
        return None
    if host.startswith("www."):
        host = host[4:]
    scheme = parts.scheme.lower()
    port = str(parts.port) if parts.port else ""
    if port and port == _DEFAULT_PORTS.get(scheme):
        port = ""
    netloc = f"{host}:{port}" if port else host
    path = parts.path.rstrip("/")
    query = sorted(
        (key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not _is_tracking_param(key)
    )
    query_string = "&".join(f"{key}={value}" for key, value in query)
    canonical_scheme = _CANONICAL_WEB_SCHEME if scheme in _WEB_SCHEMES else scheme
    return urlunsplit((canonical_scheme, netloc, path, query_string, ""))


def as_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to a naive datetime.

    SQLite has no timezone type, so a value written as aware UTC comes back
    naive. Comparing the two raises ``TypeError``, which would otherwise blow
    up somewhere far away from the cause, so every datetime comparison in this
    package goes through here first.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
