"""A run must never remove a listing nothing has disproven. GitHub issue #2.

The reported symptom was that clicking "Jetzt suchen" wiped every property
added by hand through the paste box. The cause was broader than that: absence
detection asked whether a source was *allowed* to prove things and then read
"absent from this run's results" as "gone", without ever establishing that the
source had actually listed its whole inventory.

Three different situations produced the same wrong outcome, and all three are
pinned here - together with the case that must keep working, because the lazy
fix for this bug is to stop removing anything at all.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from hofradar.db.enums import ListingStatus, SourceRole
from hofradar.db.models import Property, PropertySource, Source
from hofradar.lifecycle import ingest, mark_missing
from hofradar.lifecycle.absence import EMPTY_RESULT_GUARD_MIN_ROWS
from hofradar.sources import get_adapter

# --------------------------------------------------------------------------- #
# The adapter contract: which sources can enumerate at all
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "key",
    ["manual", "csv_import", "generic_rss", "gemeindeblatt_pdf", "web_search"],
)
def test_non_enumerating_sources_declare_themselves(key: str) -> None:
    """A source that cannot list its inventory must say so on the class."""
    from hofradar.config import load_sources

    config = {s.key: s for s in load_sources()}
    adapter = get_adapter(config[key])
    assert adapter.enumerates is False
    assert adapter.can_prove_absence is False


def test_an_enumerating_source_can_prove_absence() -> None:
    from hofradar.config import load_sources

    config = {s.key: s for s in load_sources()}
    adapter = get_adapter(config["zvg_bayern"])
    assert adapter.enumerates is True
    assert adapter.can_prove_absence is True


def test_a_truncated_enumeration_withdraws_the_claim() -> None:
    from hofradar.config import load_sources

    config = {s.key: s for s in load_sources()}
    adapter = get_adapter(config["generic_sitemap"])
    assert adapter.can_prove_absence is True

    adapter.mark_enumeration_incomplete("max_pages reached")
    assert adapter.can_prove_absence is False

    adapter.begin_enumeration()
    assert adapter.can_prove_absence is True


# --------------------------------------------------------------------------- #
# mark_missing itself
# --------------------------------------------------------------------------- #


#: Deliberately far apart in town, postcode, price and area. Listings that look
#: alike are *supposed* to be merged into one property by the deduplicator, and
#: a fixture that trips that would test nothing about absence detection.
_DISTINCT_PLACES = [
    ("Vogtareuth", "83569", 495_000.0, 2_100.0, 1891),
    ("Bad Aibling", "83043", 730_000.0, 5_400.0, 1874),
    ("Wasserburg am Inn", "83512", 385_000.0, 900.0, 1935),
    ("Bruckmuehl", "83052", 640_000.0, 7_800.0, 1912),
    ("Grosskarolinenfeld", "83109", 555_000.0, 3_300.0, 1960),
]


def _seed(db_session, make_source, make_listing, *, count: int = 1) -> tuple[Source, list[Property]]:
    source = make_source(key="broker", role=SourceRole.PRIMARY)
    props: list[Property] = []
    for index in range(count):
        town, postcode, price, land, year = _DISTINCT_PLACES[index]
        listing = make_listing(
            url=f"https://broker.invalid/objekt-{index}",
            title=f"Hofstelle in {town}",
            town=town,
            postcode=postcode,
            price=price,
            land_sqm=land,
            year_built=year,
        )
        prop, _ = ingest(db_session, listing, source=source, run_id=1)
        props.append(prop)
    db_session.flush()
    assert len({p.id for p in props}) == count, "fixture listings must not deduplicate"
    return source, props


def test_an_incomplete_enumeration_removes_nothing(db_session, make_source, make_listing) -> None:
    source, props = _seed(db_session, make_source, make_listing)

    changes = mark_missing(
        db_session, set(), source=source, run_id=2, enumeration_complete=False
    )

    assert changes == []
    assert props[0].listing_status == ListingStatus.ACTIVE
    assert props[0].removed_at is None


def test_a_complete_enumeration_still_removes_a_genuinely_gone_listing(
    db_session, make_source, make_listing
) -> None:
    """The guard against the lazy fix: real removals must keep working."""
    source, props = _seed(db_session, make_source, make_listing)

    changes = mark_missing(
        db_session, set(), source=source, run_id=2, enumeration_complete=True
    )

    assert len(changes) == 1
    assert props[0].listing_status == ListingStatus.REMOVED
    assert props[0].removed_at is not None


def test_a_source_going_from_many_to_zero_is_treated_as_broken(
    db_session, make_source, make_listing
) -> None:
    """Silent parser rot returns HTTP 200 and no results. Do not obey it."""
    source, props = _seed(
        db_session, make_source, make_listing, count=EMPTY_RESULT_GUARD_MIN_ROWS
    )

    changes = mark_missing(
        db_session, set(), source=source, run_id=2, enumeration_complete=True
    )

    assert changes == []
    assert all(p.listing_status == ListingStatus.ACTIVE for p in props)


def test_a_partial_result_still_removes_only_what_is_missing(
    db_session, make_source, make_listing
) -> None:
    source, props = _seed(
        db_session, make_source, make_listing, count=EMPTY_RESULT_GUARD_MIN_ROWS + 1
    )
    still_listed = {p.id for p in props[1:]}

    changes = mark_missing(
        db_session, still_listed, source=source, run_id=2, enumeration_complete=True
    )

    assert len(changes) == 1
    assert props[0].listing_status == ListingStatus.REMOVED
    assert all(p.listing_status == ListingStatus.ACTIVE for p in props[1:])


def test_a_discovery_source_is_still_ignored_entirely(
    db_session, make_source, make_listing
) -> None:
    """The pre-existing invariant must survive the fix."""
    source = make_source(key="aggregator", role=SourceRole.DISCOVERY)
    listing = make_listing(url="https://aggregator.invalid/1", town="Vogtareuth")
    prop, _ = ingest(db_session, listing, source=source, run_id=1)

    changes = mark_missing(
        db_session, set(), source=source, run_id=2, enumeration_complete=True
    )

    assert changes == []
    assert prop.listing_status != ListingStatus.REMOVED


# --------------------------------------------------------------------------- #
# The reported symptom
# --------------------------------------------------------------------------- #


def test_a_hand_added_property_survives_a_run(db_session, make_source, make_listing) -> None:
    """The paste box: role primary, but it never enumerates anything."""
    from hofradar.config import load_sources

    manual_config = {s.key: s for s in load_sources()}["manual"]
    source = make_source(key="manual", role=SourceRole.PRIMARY)
    listing = make_listing(
        url="https://example.invalid/paste-1",
        title="Sacherl mit Stadel bei Vogtareuth",
        town="Vogtareuth",
        postcode="83569",
    )
    prop, _ = ingest(db_session, listing, source=source, run_id=1)

    adapter = get_adapter(manual_config)
    changes = mark_missing(
        db_session,
        set(),
        source=source,
        run_id=2,
        enumeration_complete=adapter.can_prove_absence,
    )

    assert changes == []
    assert prop.listing_status == ListingStatus.ACTIVE
    assert prop.removed_at is None


def test_the_property_source_row_stays_visible_too(
    db_session, make_source, make_listing
) -> None:
    """Not just the status: the per-source visibility flag must not be cleared."""
    source, props = _seed(db_session, make_source, make_listing)

    mark_missing(db_session, set(), source=source, run_id=2, enumeration_complete=False)

    row = db_session.scalar(
        select(PropertySource).where(PropertySource.property_id == props[0].id)
    )
    assert row.last_listing_visible is True
