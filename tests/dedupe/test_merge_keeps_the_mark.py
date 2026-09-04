"""A merge must never un-mark a farm the reader put on the Merkliste."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hofradar.dedupe import merge_properties
from hofradar.dedupe._util import as_utc


def test_merge_keeps_the_losers_mark(db_session, make_property):
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    keep = make_property(shortlisted_at=None)
    drop = make_property(shortlisted_at=t0)

    merged = merge_properties(db_session, keep, drop)

    # SQLite drops the tz on read, same as every other timestamp here.
    assert as_utc(merged.shortlisted_at) == t0


def test_merge_keeps_the_earlier_timestamp_when_both_are_marked(db_session, make_property):
    earlier = datetime(2026, 1, 1, tzinfo=UTC)
    later = earlier + timedelta(days=10)
    keep = make_property(shortlisted_at=later)
    drop = make_property(shortlisted_at=earlier)

    merged = merge_properties(db_session, keep, drop)

    assert as_utc(merged.shortlisted_at) == earlier
