"""Tag and text folding, private to :mod:`hofradar.costmodel`.

``hofradar.normalize`` owns the canonical implementation, but the module API
contract says packages talk to each other only through their published names,
and the cost model needs nothing more than "does this tag list mention this
term". Twelve duplicated lines are cheaper than a cross-package dependency.
"""

from __future__ import annotations

from typing import Any, Iterable

#: German folding. Applied after casefolding, so only lowercase forms are needed.
_UMLAUT_MAP = str.maketrans(
    {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
        "-": " ",
        "/": " ",
        "_": " ",
    }
)


def fold(text: str | None) -> str:
    """Casefold, expand umlauts, squash whitespace. Empty string for ``None``."""
    if not text:
        return ""
    return " ".join(text.casefold().translate(_UMLAUT_MAP).split())


def fold_all(values: Iterable[Any] | None) -> list[str]:
    """Fold every element of a JSON tag list, dropping non-strings and blanks."""
    if not values:
        return []
    return [folded for value in values if (folded := fold(str(value)))]


def contains_any(haystack: str, terms: Iterable[str]) -> str | None:
    """Return the first term found in ``haystack``, or ``None``."""
    for term in terms:
        if term in haystack:
            return term
    return None
