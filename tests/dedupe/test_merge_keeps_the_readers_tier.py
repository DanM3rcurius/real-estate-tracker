"""The reader's pinned Sanierungsstufe survives a merge like ``user_title`` does.

``user_renovation_tier`` sits in ``dedupe.merge._FILLABLE_FIELDS`` beside
``user_title``: the survivor keeps its own pin, and inherits the dropped row's
only when it has none of its own. Losing a reader's judgement about the
building's condition during a routine dedupe merge would be exactly the kind
of silent data loss CLAUDE.md warns about.
"""

from __future__ import annotations

from hofradar.dedupe import merge_properties


def test_the_survivor_keeps_its_own_tier(db_session, make_property):
    keep = make_property(user_renovation_tier="light")
    drop = make_property(user_renovation_tier="heavy")

    merged = merge_properties(db_session, keep, drop)

    assert merged.user_renovation_tier == "light"


def test_the_survivor_inherits_the_dropped_rows_tier_when_it_has_none(db_session, make_property):
    keep = make_property(user_renovation_tier=None)
    drop = make_property(user_renovation_tier="heavy")

    merged = merge_properties(db_session, keep, drop)

    assert merged.user_renovation_tier == "heavy"


def test_a_merge_without_any_tier_leaves_it_none(db_session, make_property):
    keep = make_property(user_renovation_tier=None)
    drop = make_property(user_renovation_tier=None)

    merged = merge_properties(db_session, keep, drop)

    assert merged.user_renovation_tier is None
