"""sync_sources_to_db must carry every SourceConfig field onto the ORM row.

A field the sync forgets is not "unset" - it silently pins the field to its
ORM default forever, however the YAML reads, because nothing else in the
pipeline ever writes to the ``sources`` table. ``listing_ttl_days`` earns a
dedicated test for exactly that reason: it read correctly from YAML, was
sitting on ``SourceConfig``, and was still never reaching the database.
"""

from __future__ import annotations

from hofradar.sources import sync_sources_to_db


def test_listing_ttl_days_reaches_the_source_row(session, make_source_config) -> None:
    cfg = make_source_config(key="ovbimmo", role="local", listing_ttl_days=14)

    rows = sync_sources_to_db(session, [cfg])

    assert rows[0].listing_ttl_days == 14


def test_listing_ttl_days_none_stays_none(session, make_source_config) -> None:
    cfg = make_source_config(key="denkmalboerse", role="primary")

    rows = sync_sources_to_db(session, [cfg])

    assert rows[0].listing_ttl_days is None


def test_re_syncing_updates_an_existing_row(session, make_source_config) -> None:
    """A config reload must not leave a stale TTL behind, either direction."""
    cfg = make_source_config(key="ovbimmo", role="local", listing_ttl_days=14)
    sync_sources_to_db(session, [cfg])

    cfg_updated = make_source_config(key="ovbimmo", role="local", listing_ttl_days=None)
    rows = sync_sources_to_db(session, [cfg_updated])

    assert rows[0].listing_ttl_days is None
