"""Blueprint Test 3, end to end through ingest.

The dedupe verdict decides how many Property rows exist. Three uncorroborated
Vogtareuth listings must stay three rows with a ``needs_review`` note on each,
because auto-merging them would silently destroy two farms; the same three
listings *with* corroborating numbers must collapse into one.
"""

from __future__ import annotations

from hofradar.db.enums import ChangeKind, SourceRole
from hofradar.db.models import Property, StatusHistory
from hofradar.lifecycle import ingest

TITLES = ["Hofstelle in Vogtareuth", "Bauernhaus Vogtareuth", "Sacherl bei Vogtareuth"]


def test_uncorroborated_lookalikes_are_not_auto_merged(db_session, make_source, make_listing):
    source = make_source("gemeindeblatt", role=SourceRole.LOCAL)

    for index, title in enumerate(TITLES):
        ingest(
            db_session,
            make_listing(
                source_key=source.key,
                url=f"https://gemeindeblatt.example/{index}",
                title=title,
                land_sqm=None,
                living_sqm=None,
                price=None,
            ),
            source=source,
            run_id=1,
        )

    assert db_session.query(Property).count() == 3

    details = [
        h.detail or ""
        for h in db_session.query(StatusHistory)
        .filter_by(change_kind=ChangeKind.FIRST_SEEN)
        .all()
    ]
    # The two later rows record why they were not merged, so a human can look.
    assert sum("needs_review" in d for d in details) == 2


def test_corroborated_lookalikes_collapse_into_one(db_session, make_source, make_listing):
    source = make_source("gemeindeblatt", role=SourceRole.LOCAL)
    numbers = [(8500, 220, 790_000), (8620, 214, 789_000), (8450, 223, 795_000)]

    for index, (title, (land, living, price)) in enumerate(zip(TITLES, numbers, strict=True)):
        ingest(
            db_session,
            make_listing(
                source_key=source.key,
                url=f"https://gemeindeblatt.example/{index}",
                title=title,
                land_sqm=land,
                living_sqm=living,
                price=price,
                year_built=1890,
            ),
            source=source,
            run_id=1,
        )

    assert db_session.query(Property).count() == 1


def test_ingest_follows_a_merge_instead_of_resurrecting_the_dropped_row(
    db_session, make_source, make_listing
):
    """Once a human merges two look-alikes, re-crawling either URL must land on
    the survivor - the same source and URL is proof, whatever the fuzzy score."""
    from hofradar.dedupe import merge_properties

    source = make_source("gemeindeblatt", role=SourceRole.LOCAL)
    listing_a = make_listing(
        source_key=source.key, url="https://gemeindeblatt.example/0", title=TITLES[0],
        land_sqm=None, living_sqm=None, price=None,
    )
    listing_b = make_listing(
        source_key=source.key, url="https://gemeindeblatt.example/2", title=TITLES[2],
        land_sqm=None, living_sqm=None, price=None,
    )

    keep, _ = ingest(db_session, listing_a, source=source, run_id=1)
    drop, _ = ingest(db_session, listing_b, source=source, run_id=1)
    assert keep.id != drop.id  # correctly left for review, not auto-merged

    merge_properties(db_session, keep, drop)

    same, change = ingest(db_session, listing_b, source=source, run_id=2)

    assert same.id == keep.id
    assert change.kind != ChangeKind.FIRST_SEEN
    assert db_session.query(Property).filter(Property.merged_into_id.is_(None)).count() == 1
