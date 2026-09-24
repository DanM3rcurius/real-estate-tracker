"""The reader's Sanierungsstufe survives a merge, like their title and note.

A merge is where triage data is most easily lost. The survivor keeps its own
tier and inherits the dropped row's only when it has none.
"""

from __future__ import annotations

from hofradar.dedupe import merge_properties


def test_the_survivor_inherits_the_dropped_rows_tier(db_session, make_property):
    keep = make_property(user_renovation_tier=None)
    drop = make_property(user_renovation_tier="heavy")

    merged = merge_properties(db_session, keep, drop)

    assert merged.user_renovation_tier == "heavy"


def test_the_survivors_own_tier_wins(db_session, make_property):
    keep = make_property(user_renovation_tier="light")
    drop = make_property(user_renovation_tier="complete")

    merged = merge_properties(db_session, keep, drop)

    assert merged.user_renovation_tier == "light"
