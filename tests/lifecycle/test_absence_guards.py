"""mark_missing must not turn a parser failure into a market event.

A source that suddenly reports nothing has either lost its whole inventory in
one week - which never happens - or stopped parsing. The second is
overwhelmingly more likely, and the damage is written into the append-only
observation history where it cannot be distinguished from the truth later.
"""

from __future__ import annotations

import pytest

from hofradar.db.enums import ListingStatus, SourceRole
from hofradar.lifecycle import ImplausibleAbsence, ingest, mark_missing

#: Deliberately spread across town, postcode, land and year so the
#: deduplicator never folds them into one property - a collision here would
#: test dedupe, not absence detection.
_DISTINCT_PLACES = [
    ("Vogtareuth", "83569", 2_100.0, 1891),
    ("Bad Aibling", "83043", 5_400.0, 1874),
    ("Wasserburg am Inn", "83512", 900.0, 1935),
    ("Bruckmuehl", "83052", 7_800.0, 1912),
    ("Grosskarolinenfeld", "83109", 3_300.0, 1960),
    ("Rott am Inn", "83543", 4_100.0, 1902),
    ("Amerang", "83123", 6_600.0, 1888),
    ("Rosenheim", "83022", 1_500.0, 1955),
    ("Prien am Chiemsee", "83209", 8_900.0, 1920),
    ("Feldkirchen-Westerham", "83620", 2_900.0, 1948),
]


def _seed(session, make_source, make_listing, *, count: int):
    """Ingest ``count`` distinct properties, all carried by one primary source."""
    source = make_source(key="denkmalboerse", role=SourceRole.PRIMARY)
    props = []
    for index in range(count):
        town, postcode, land, year = _DISTINCT_PLACES[index]
        listing = make_listing(
            source_key=source.key,
            url=f"https://denkmalboerse.example/objekt-{index}",
            town=town,
            postcode=postcode,
            land_sqm=land,
            year_built=year,
        )
        prop, _ = ingest(session, listing, source=source, run_id=1)
        props.append(prop)
    session.flush()
    assert len({p.id for p in props}) == count, "fixture listings must not deduplicate"
    return source, props


def test_empty_seen_set_raises_instead_of_removing_everything(
    session, make_source, make_listing
) -> None:
    source, _ = _seed(session, make_source, make_listing, count=3)

    with pytest.raises(ImplausibleAbsence, match="saw nothing"):
        mark_missing(session, set(), source=source, enumeration_complete=True)


def test_removing_more_than_the_threshold_raises(session, make_source, make_listing) -> None:
    source, props = _seed(session, make_source, make_listing, count=10)

    # Saw 5 of 10 - half the inventory vanished in one run.
    seen = {p.id for p in props[:5]}
    with pytest.raises(ImplausibleAbsence, match="50"):
        mark_missing(session, seen, source=source, enumeration_complete=True)


def test_a_normal_single_removal_still_works(session, make_source, make_listing) -> None:
    source, props = _seed(session, make_source, make_listing, count=10)

    seen = {p.id for p in props[:9]}
    changes = mark_missing(session, seen, source=source, enumeration_complete=True)

    assert len(changes) == 1
    assert props[9].listing_status == ListingStatus.REMOVED
