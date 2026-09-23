"""The search box finds a property by the reader's name and by the advert's words.

Renaming a farm must not make it unfindable under the words the listing uses,
and a reader who called it "Moarhof" will type "Moarhof". Both names go into
``matches_search``'s haystack. A property nobody renamed must behave exactly as
before - in particular its empty ``user_title`` must not become the string
``"None"`` in the haystack.
"""

from __future__ import annotations

import pytest

from hofradar.search import SEARCH_FIELDS, matches_search


def test_the_readers_name_is_a_search_field() -> None:
    assert "user_title" in SEARCH_FIELDS
    assert "canonical_title" in SEARCH_FIELDS


@pytest.mark.parametrize("needle", ["Moarhof", "moarhof", "MOAR", "  moarhof  "])
def test_finds_the_property_by_the_readers_name(make_property, needle: str) -> None:
    prop = make_property(canonical_title="Hofstelle mit Nebengebaeuden", user_title="Moarhof")

    assert matches_search(prop, needle)


@pytest.mark.parametrize("needle", ["Hofstelle", "nebengebaeuden", "Vogtareuth", "83569"])
def test_still_finds_a_renamed_property_by_the_listings_words(
    make_property, needle: str
) -> None:
    prop = make_property(canonical_title="Hofstelle mit Nebengebaeuden", user_title="Moarhof")

    assert matches_search(prop, needle)


def test_a_property_without_a_readers_name_searches_as_before(make_property) -> None:
    prop = make_property(canonical_title="Hofstelle mit Nebengebaeuden", user_title=None)

    assert matches_search(prop, "Hofstelle")
    assert not matches_search(prop, "Moarhof")
    assert not matches_search(prop, "None")


def test_the_readers_name_does_not_match_another_property(make_property) -> None:
    named = make_property(user_title="Moarhof")
    other = make_property(user_title="Hof am Waldrand")

    assert matches_search(named, "Moarhof")
    assert not matches_search(other, "Moarhof")
