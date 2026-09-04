"""mark_missing must not turn a parser failure into a market event.

A source that suddenly reports nothing has either lost its whole inventory in
one week - which never happens - or stopped parsing. The second is
overwhelmingly more likely, and the damage is written into the append-only
observation history where it cannot be distinguished from the truth later.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from hofradar.db.enums import ChangeKind, ListingStatus, SourceRole
from hofradar.db.models import PropertySource
from hofradar.lifecycle import ImplausibleAbsence, ingest, mark_missing

#: Older than any ``listing_ttl_days`` a source in the registry sets, so every
#: advert in the TTL test below is unambiguously past its paid window.
_PAST_ANY_TTL_DAYS = 60

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
    source, props = _seed(session, make_source, make_listing, count=3)

    with pytest.raises(ImplausibleAbsence, match="saw nothing"):
        mark_missing(session, set(), source=source, enumeration_complete=True)

    # "so nothing is written" (ImplausibleAbsence's own docstring) - not just
    # that the exception fired, but that the raise happened before any row
    # was touched.
    assert all(p.listing_status == ListingStatus.ACTIVE for p in props)


def test_removing_more_than_the_threshold_raises(session, make_source, make_listing) -> None:
    source, props = _seed(session, make_source, make_listing, count=10)

    # Saw 5 of 10 - half the inventory vanished in one run.
    seen = {p.id for p in props[:5]}
    with pytest.raises(ImplausibleAbsence, match="50"):
        mark_missing(session, seen, source=source, enumeration_complete=True)

    assert all(p.listing_status == ListingStatus.ACTIVE for p in props)


def test_a_normal_single_removal_still_works(session, make_source, make_listing) -> None:
    source, props = _seed(session, make_source, make_listing, count=10)

    seen = {p.id for p in props[:9]}
    changes = mark_missing(session, seen, source=source, enumeration_complete=True)

    assert len(changes) == 1
    assert props[9].listing_status == ListingStatus.REMOVED


def test_two_row_empty_seen_set_still_raises(session, make_source, make_listing) -> None:
    """The case the old size gate (>= 3 rows) let straight through: a source
    with only two visible listings - the modal inventory for a search DNA
    this narrow, not an edge case - reporting zero. The empty-seen-set guard
    is unconditional precisely so this size no longer gets a free pass.
    """
    source, props = _seed(session, make_source, make_listing, count=2)

    with pytest.raises(ImplausibleAbsence, match="saw nothing"):
        mark_missing(session, set(), source=source, enumeration_complete=True)

    assert all(p.listing_status == ListingStatus.ACTIVE for p in props)


def test_a_ttl_source_that_saw_nothing_is_refused_like_any_other(
    session, make_source, make_listing
) -> None:
    """The empty-seen-set guard must sit BEFORE the TTL expiry split.

    A source with ``listing_ttl_days`` has every one of its aged-out rows
    reclassified as EXPIRED before either plausibility guard would otherwise
    look at them. Gate the empty-seen-set guard on what survives that split
    and a TTL source has *no* absence guard left at all: a template change
    that parses zero listings walks straight through and writes one false
    EXPIRED StatusHistory row per listing, then returns ``[]`` - a run
    indistinguishable from a quiet one, with the fiction now permanent in the
    append-only history.

    A ``listing_ttl_days`` explains why an individual advert vanished. It
    explains nothing about why the run produced no rows at all, so it may not
    excuse one. The genuine fortnightly mass-expiry this must not break is a
    different shape - some ads age out while the rest of the inventory is
    still listed, so the seen-set is not empty - and it is pinned separately
    in ``tests/lifecycle/test_listing_ttl.py``.
    """
    source = make_source(key="ovbimmo", role=SourceRole.LOCAL, listing_ttl_days=14)
    props = []
    for index, (town, postcode, land, year) in enumerate(_DISTINCT_PLACES[:4]):
        listing = make_listing(
            source_key=source.key,
            url=f"https://ovbimmo.example/immobilien/objekt-{index}",
            town=town,
            postcode=postcode,
            land_sqm=land,
            year_built=year,
        )
        prop, _ = ingest(session, listing, source=source, run_id=1)
        props.append(prop)
    session.flush()
    # Every advert is older than the two-week window, so without the fix every
    # missing row is classified EXPIRED and neither guard ever engages.
    for ps in session.query(PropertySource).all():
        ps.first_seen = datetime.now(UTC) - timedelta(days=_PAST_ANY_TTL_DAYS)
    session.flush()

    with pytest.raises(ImplausibleAbsence, match="saw nothing"):
        mark_missing(session, set(), source=source, enumeration_complete=True)

    # Nothing written: no EXPIRED transition, no history row, no cleared
    # visibility flag. A refused run leaves the database exactly as it was.
    assert all(p.listing_status == ListingStatus.ACTIVE for p in props)
    assert not [
        row
        for prop in props
        for row in prop.status_history
        if row.new_status == ListingStatus.EXPIRED
    ]
    assert all(ps.last_listing_visible for ps in session.query(PropertySource).all())


def test_a_single_removal_on_a_three_listing_source_does_not_deadlock(
    session, make_source, make_listing
) -> None:
    """The controller ruling's deadlock case. 1 missing of 3 is 33% - over
    IMPLAUSIBLE_ABSENCE_FRACTION - but below IMPLAUSIBLE_ABSENCE_MIN_MISSING,
    so the fraction guard must not engage: the removal has to actually
    succeed, and it has to keep succeeding on the next run over the same
    seen-set rather than raising forever (a refused run writes nothing, so
    without the floor the same "still visible" row would trip the guard
    again every time).
    """
    source, props = _seed(session, make_source, make_listing, count=3)
    seen = {p.id for p in props[1:]}

    changes = mark_missing(session, seen, source=source, enumeration_complete=True)

    assert len(changes) == 1
    assert changes[0].kind == ChangeKind.REMOVED
    assert props[0].listing_status == ListingStatus.REMOVED
    assert props[0].removed_at is not None

    # The "forever" half of the deadlock: the same seen-set, run again, must
    # complete quietly - not raise a second time.
    again = mark_missing(session, seen, source=source, enumeration_complete=True)
    assert again == []
