"""A re-crawl keeps the listing's condition current and never touches the reader's pin.

``user_renovation_tier`` is triage-class data like ``user_title``: set only
from the dossier (``POST /property/{id}/sanierungsstufe``), never written by
``ingest``. A re-crawl that updates the listing's own condition (which feeds
``listing_renovation_tier``, not the reader's pin) must not overwrite or clear
the reader's judgement about the building.
"""

from __future__ import annotations

from hofradar.db.enums import ChangeKind, SourceRole
from hofradar.db.models import Property
from hofradar.lifecycle import ingest

_URL = "https://bauernhoefe.example/objekt/4711"
_READERS_TIER = "heavy"


def test_ingest_never_writes_a_user_renovation_tier(db_session, make_source, make_listing):
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)

    prop, change = ingest(
        db_session,
        make_listing(source_key=source.key, url=_URL, building_features=["saniert"]),
        source=source,
    )

    assert change.kind == ChangeKind.FIRST_SEEN
    assert prop.user_renovation_tier is None


def test_a_recrawl_with_changed_condition_tags_keeps_the_readers_tier(
    db_session, make_source, make_listing
):
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)
    facts = dict(source_key=source.key, url=_URL)

    prop, first = ingest(
        db_session,
        make_listing(building_features=["renovierungsbeduerftig"], **facts),
        source=source,
    )
    assert first.kind == ChangeKind.FIRST_SEEN
    prop.user_renovation_tier = _READERS_TIER
    db_session.flush()

    same, change = ingest(
        db_session,
        make_listing(building_features=["saniert"], **facts),
        source=source,
        run_id=2,
    )

    assert same.id == prop.id
    assert change.kind != ChangeKind.FIRST_SEEN
    assert "saniert" in same.building_features
    assert same.user_renovation_tier == _READERS_TIER

    db_session.expire_all()
    stored = db_session.get(Property, prop.id)
    assert stored is not None
    assert "saniert" in stored.building_features
    assert stored.user_renovation_tier == _READERS_TIER
