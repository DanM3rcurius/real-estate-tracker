"""``gazetteer.lookup``: word-boundary matching, not bare substring matching.

The gazetteer is consulted through ``town_in_radius``, a pre-filter that may
only ever save a network fetch, never cause one to be skipped for a property
that is actually in range. A bare substring match breaks that guarantee: a
town whose name happens to *contain* a gazetteer entry's name as a run of
characters (not as whole words) would resolve to the wrong, unrelated entry.
"""

from __future__ import annotations

from hofradar.geo.gazetteer import lookup


def test_fuerstenfeldbruck_does_not_resolve_to_bruck() -> None:
    """Load-bearing case: this is the actual measured bug.

    Fuerstenfeldbruck is a real town near Munich, roughly 55 km from the
    project's origin. The gazetteer's only "Bruck" entry (Landkreis
    Ebersberg, ~18 km from the origin) is a *different* place that merely
    shares a bare character run with the end of "Fuerstenfeldbruck" - there
    is no word boundary between "feld" and "bruck" in that name. Under the
    old plain-substring implementation, ``lookup("Fuerstenfeldbruck")``
    returned the "Bruck" entry, which made ``town_in_radius`` report a truly
    in-range town as out of range and silently skip the fetch for it - the
    one failure the three-valued pre-filter design exists to prevent. The
    gazetteer genuinely does not know Fuerstenfeldbruck, so the correct
    answer is None, not a wrong entry.
    """
    assert lookup("Fürstenfeldbruck") is None


def test_bruck_alone_still_resolves() -> None:
    """The real "Bruck" entry must still match on its own name."""
    entry = lookup("Bruck")
    assert entry is not None
    assert entry.name == "Bruck"


def test_compound_word_does_not_leak_a_shorter_entry_name() -> None:
    """More instances of the same failure mode, all real Bavarian places.

    None of these towns are in the bundled gazetteer, but each one's name
    contains a real gazetteer entry's name as a bare run of characters with
    no word boundary around it - "Seeon-Seebruck" ends in "...seebruck",
    not "... bruck"; "Bruckberg" starts with "bruck" but continues straight
    into "berg" with no separator. A correct matcher must miss all of them.
    """
    assert lookup("Seeon-Seebruck") is None  # contains "...bruck", not "Bruck"
    assert lookup("Bruckberg") is None  # starts with "Bruck" but glued to "berg"
    assert lookup("Neuaibling") is None  # would leak "Bad Aibling" via "Aibling"


def test_town_name_inside_a_longer_query_still_resolves() -> None:
    """The legitimate use case the substring behaviour exists for: town
    names are pasted into messy free-text addresses, not passed alone."""
    entry = lookup("Hauptstrasse 5, 83043 Bad Aibling")
    assert entry is not None
    assert entry.name == "Bad Aibling"


def test_multi_word_town_name_inside_a_longer_query_still_resolves() -> None:
    """Token-sequence matches (not single words) must keep working."""
    entry = lookup("83512 Wasserburg am Inn")
    assert entry is not None
    assert entry.name == "Wasserburg am Inn"


def test_hyphenated_context_around_a_town_name_still_resolves() -> None:
    """A hyphen is a real word boundary in German place-name text."""
    entry = lookup("Rosenheim-Stadt")
    assert entry is not None
    assert entry.name == "Rosenheim"


def test_more_specific_entry_wins_over_a_shorter_entry_it_contains() -> None:
    """"Tegernsee" is itself a gazetteer entry and, as a whole word, also
    appears inside the separate entry "Gmund am Tegernsee". A query for the
    longer name must resolve to the longer, more specific entry rather than
    the shorter one it happens to contain as a legitimate word match."""
    entry = lookup("83703 Gmund am Tegernsee")
    assert entry is not None
    assert entry.name == "Gmund am Tegernsee"


def test_unrelated_query_still_misses() -> None:
    assert lookup("Timbuktu, Mali") is None


def test_blank_query_is_none() -> None:
    assert lookup("") is None
    assert lookup(None) is None  # type: ignore[arg-type]
