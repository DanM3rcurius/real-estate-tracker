"""The search box's matching rule (issue #14) - shared, and shared carefully.

Casefolded Python substring, not a SQL ``LIKE``: SQLite's ``lower()`` folds
ASCII only, so ``lower('Ödhof')`` never matches a casefolded ``öd``, and a
``LIKE`` would be wrong for every umlaut village in the search area.

This lives at the top of the package, outside both ``hofradar.web`` and
``hofradar.scoring``, on purpose. ``hofradar.scoring.engine`` needs it for
``ranked_properties`` and must not import ``hofradar.web`` (the dependency
runs the other way). ``hofradar.web.query`` needs the identical rule for its
own filtering pass and must keep booting when ``hofradar.scoring`` is
missing, half-written or raising on import - see ``hofradar.web.lazy``'s
module docstring - so it cannot import the function from ``scoring`` either.
A neutral module with no dependency on either package is the only place both
sides can import from without re-introducing the coupling this split exists
to avoid.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from hofradar.db.models import Property

#: Fields a reader could plausibly type into the search box, in the order
#: :func:`matches_search` joins them into one haystack.
SEARCH_FIELDS: tuple[str, ...] = ("town", "postcode", "district", "canonical_title")


def matches_search(prop: Property, needle: str) -> bool:
    """Casefolded substring over the fields a reader would type (issue #14)."""
    wanted = needle.casefold().strip()
    if not wanted:
        return True
    haystack = " ".join(str(getattr(prop, f, None) or "") for f in SEARCH_FIELDS).casefold()
    return wanted in haystack
