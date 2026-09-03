"""Deduplication: five listings of one farm must become one property.

The package is deliberately split into a cheap, indexable *blocking* step
(:mod:`~hofradar.dedupe.fingerprint`, :mod:`~hofradar.dedupe.find`) and an
expensive, evidence-based *decision* step (:mod:`~hofradar.dedupe.compare`).
The fingerprint is coarse on purpose and proves nothing; ``compare`` is the
only thing allowed to say "same object", and it will not say it on the strength
of similar words alone.

Public API (see docs/MODULE_API.md)::

    fingerprint(listing_or_property) -> str
    find_duplicate(session, listing, *, lat=None, lon=None) -> DuplicateVerdict
    compare(a, b) -> DuplicateVerdict
    merge_properties(session, keep, drop) -> Property
"""

from __future__ import annotations

from hofradar.dedupe.compare import compare
from hofradar.dedupe.find import find_duplicate
from hofradar.dedupe.fingerprint import fingerprint
from hofradar.dedupe.merge import merge_properties

__all__ = ["compare", "find_duplicate", "fingerprint", "merge_properties"]
