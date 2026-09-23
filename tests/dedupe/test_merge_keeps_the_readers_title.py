"""The reader's name for a farm: carried across a merge, never used to decide one.

A merge is where triage data is most easily lost, so ``user_title`` is filled
like ``user_note`` - the survivor keeps its own, and inherits the dropped row's
only when it has none. The opposite direction matters as much: the name is the
reader's shorthand, not a fact about the listing, so dedupe must keep comparing
the advert's own words. Two farms the reader happened to call "Moarhof" are not
one farm.
"""

from __future__ import annotations

from hofradar.dedupe import merge_properties
from hofradar.dedupe._facts import facts_of


def test_the_survivor_inherits_the_dropped_rows_name(db_session, make_property):
    keep = make_property(user_title=None)
    drop = make_property(user_title="Moarhof")

    merged = merge_properties(db_session, keep, drop)

    assert merged.user_title == "Moarhof"
    assert merged.display_title == "Moarhof"


def test_a_blank_name_on_the_survivor_counts_as_none(db_session, make_property):
    keep = make_property(user_title="")
    drop = make_property(user_title="Moarhof")

    merged = merge_properties(db_session, keep, drop)

    assert merged.user_title == "Moarhof"


def test_the_survivors_own_name_wins_when_both_have_one(db_session, make_property):
    keep = make_property(user_title="Moarhof")
    drop = make_property(user_title="Hof am Waldrand")

    merged = merge_properties(db_session, keep, drop)

    assert merged.user_title == "Moarhof"


def test_a_merge_without_any_name_leaves_the_listing_title(db_session, make_property):
    keep = make_property(canonical_title="Hofstelle in Vogtareuth", user_title=None)
    drop = make_property(canonical_title="Bauernhaus mit Stadel", user_title=None)

    merged = merge_properties(db_session, keep, drop)

    assert merged.user_title is None
    assert merged.display_title == "Hofstelle in Vogtareuth"


def test_dedupe_compares_the_listing_title_not_the_readers_name(make_property):
    prop = make_property(canonical_title="Hofstelle in Vogtareuth", user_title="Moarhof")

    assert facts_of(prop).title == "Hofstelle in Vogtareuth"
