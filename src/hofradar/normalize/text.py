"""Text normalisation primitives shared by every other parser in this package.

German source text arrives in wildly inconsistent casing, umlaut encoding, and
punctuation. Every downstream matcher (keyword extraction, price-type
detection, deduplication hashing) needs one canonical, ASCII-safe form to
compare against - otherwise "Stadl" and "STADL" and "Stadl." would all be
treated as different tokens. ``normalize_text`` is that canonical form, and
``text_hash`` is its content-addressed fingerprint (used by the dedupe stage
to spot byte-identical listings even when whitespace/casing drifted).
"""

from __future__ import annotations

import hashlib
import re

#: ä/ö/ü fold to their digraph spelling; ß already becomes "ss" under
#: str.casefold() (Python's casefold implements full Unicode case folding,
#: which maps ß -> "ss"), so it is not repeated here.
_UMLAUT_MAP = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue"})

#: Anything that is not a "word" character (letter/digit/underscore) or
#: whitespace is punctuation for our purposes and becomes a space, so that
#: "Feldkirchen-Westerham" and "Feldkirchen -  Westerham" normalise the same.
_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str | None) -> str:
    """Casefold, fold umlauts to their digraphs, and collapse punctuation.

    Steps, in order: casefold (also turns ß -> "ss") -> fold ä/ö/ü to
    ae/oe/ue -> replace any remaining punctuation with a single space ->
    collapse repeated whitespace -> strip.

    ``None`` and the empty string both normalise to ``""``.
    """
    if not text:
        return ""
    folded = text.casefold().translate(_UMLAUT_MAP)
    folded = _PUNCTUATION_RE.sub(" ", folded)
    return _WHITESPACE_RE.sub(" ", folded).strip()


def slugify(term: str) -> str:
    """Turn a German keyword/phrase into a canonical lowercase ASCII tag.

    ``slugify("großes Grundstück") == "grosses_grundstueck"``. Used to derive
    the canonical feature/signal tags from the raw strings in
    ``config/keywords.yaml`` so the vocabulary can change without touching
    code, and callers get a stable, dependable tag spelling either way.
    """
    return normalize_text(term).replace(" ", "_")


def text_hash(text: str | None) -> str:
    """Blake2s hex digest of ``normalize_text(text)``.

    Content-addressed identity for a listing's text: two raw strings that
    differ only in whitespace, casing, or umlaut encoding hash identically.
    """
    normalized = normalize_text(text)
    return hashlib.blake2s(normalized.encode("utf-8")).hexdigest()
