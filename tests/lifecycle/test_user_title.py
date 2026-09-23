"""A re-crawl keeps the listing's words current and never touches the reader's name.

``canonical_title`` is what the advert says, and ``ingest`` rewrites it when a
verifying source rewords the advert. ``user_title`` is what the reader called
the place, and it is triage-class data like ``user_note``: the moment a crawl
could overwrite it, renaming a property would last exactly until the next run.
Both facts have to survive the same re-ingest, so both are asserted on the same
row - and on the row read back from the database, not only the one in memory.
"""

from __future__ import annotations

from datetime import UTC, datetime

from hofradar.db.enums import ChangeKind, SourceRole
from hofradar.db.models import Property
from hofradar.lifecycle import changes_since, ingest

_URL = "https://bauernhoefe.example/objekt/4711"
_OLD_TITLE = "Hofstelle in Vogtareuth"
_NEW_TITLE = "Provisionsfrei: Vierseithof bei Rosenheim"
_READERS_NAME = "Moarhof"


def test_ingest_never_writes_a_user_title(db_session, make_source, make_listing):
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)

    prop, change = ingest(
        db_session, make_listing(source_key=source.key, url=_URL, title=_OLD_TITLE), source=source
    )

    assert change.kind == ChangeKind.FIRST_SEEN
    assert prop.user_title is None
    assert prop.display_title == _OLD_TITLE


def test_a_recrawl_updates_the_listing_title_but_keeps_the_readers_name(
    db_session, make_source, make_listing
):
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)
    facts = dict(source_key=source.key, url=_URL, land_sqm=8500, living_sqm=220, price=790_000)

    prop, first = ingest(db_session, make_listing(title=_OLD_TITLE, **facts), source=source)
    assert first.kind == ChangeKind.FIRST_SEEN
    prop.user_title = _READERS_NAME
    db_session.flush()

    same, change = ingest(
        db_session, make_listing(title=_NEW_TITLE, **facts), source=source, run_id=2
    )

    assert same.id == prop.id
    assert change.kind != ChangeKind.FIRST_SEEN
    assert same.canonical_title == _NEW_TITLE
    assert same.user_title == _READERS_NAME
    assert same.display_title == _READERS_NAME

    db_session.expire_all()
    stored = db_session.get(Property, prop.id)
    assert stored is not None
    assert stored.canonical_title == _NEW_TITLE
    assert stored.user_title == _READERS_NAME


def test_the_change_feed_names_the_property_the_readers_way(
    db_session, make_source, make_listing
):
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)
    prop, _ = ingest(
        db_session, make_listing(source_key=source.key, url=_URL, title=_OLD_TITLE), source=source
    )
    prop.user_title = _READERS_NAME
    db_session.flush()

    entries = changes_since(db_session, datetime(2000, 1, 1, tzinfo=UTC))

    assert [entry["title"] for entry in entries] == [_READERS_NAME]


def test_the_change_feed_falls_back_to_the_listing_title(db_session, make_source, make_listing):
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)
    ingest(
        db_session, make_listing(source_key=source.key, url=_URL, title=_OLD_TITLE), source=source
    )

    entries = changes_since(db_session, datetime(2000, 1, 1, tzinfo=UTC))

    assert [entry["title"] for entry in entries] == [_OLD_TITLE]
