"""A newspaper ad that ran its two weeks has expired, not sold.

The distinction is the whole point. REMOVED means the property left the
market and is a real event worth reporting; EXPIRED means a billing cycle
ended and says nothing about the farmstead at all. Conflating them fills the
change feed with a fortnightly metronome and drops live listings out of the
ranking.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hofradar.db.enums import ListingStatus, SourceRole
from hofradar.db.models import PropertySource
from hofradar.lifecycle import mark_missing

#: The newspaper's ad package: two weeks online.
_OVB_TTL_DAYS = 14


def _attach(session, prop, source, *, url: str | None = None) -> PropertySource:
    """Link ``prop`` to ``source`` as a currently-visible listing.

    ``mark_missing`` reasons entirely over ``PropertySource`` rows - a bare
    ``Property`` with no source link is not "carried by" anything and can
    never be found missing.
    """
    ps = PropertySource(
        property_id=prop.id,
        source_id=source.id,
        url=url or f"https://{source.key}.example/{prop.public_id}",
        role=source.role,
        last_listing_visible=True,
    )
    session.add(ps)
    session.flush()
    return ps


def test_a_listing_older_than_the_ttl_expires_rather_than_being_removed(
    session, make_source, make_property
) -> None:
    source = make_source(key="ovbimmo", role=SourceRole.LOCAL, listing_ttl_days=_OVB_TTL_DAYS)
    old = make_property(first_seen=datetime.now(UTC) - timedelta(days=20))
    _attach(session, old, source)

    changes = mark_missing(session, set(), source=source, enumeration_complete=True)

    assert old.listing_status == ListingStatus.EXPIRED
    assert all(c.kind != "removed" for c in changes)


def test_a_listing_younger_than_the_ttl_is_still_a_real_removal(
    session, make_source, make_property
) -> None:
    source = make_source(key="ovbimmo", role=SourceRole.LOCAL, listing_ttl_days=_OVB_TTL_DAYS)
    fresh = make_property(
        public_id="hof-fresh-0001", first_seen=datetime.now(UTC) - timedelta(days=3)
    )
    _attach(session, fresh, source)
    # A second, still-visible listing keeps the seen-set from being empty - a
    # single vanished listing is real news, not the empty-seen-set case the
    # guard above exists to catch, which this test is not exercising.
    still_here = make_property(public_id="hof-fresh-0002")
    still_here_ps = _attach(session, still_here, source, url="https://ovbimmo.example/still-here")

    mark_missing(
        session, {still_here_ps.property_id}, source=source, enumeration_complete=True
    )

    # Gone after three days is not a billing cycle - that is the seller acting.
    assert fresh.listing_status == ListingStatus.REMOVED


def test_a_source_without_a_ttl_is_unaffected(session, make_source, make_property) -> None:
    source = make_source(key="denkmalboerse", role=SourceRole.PRIMARY, listing_ttl_days=None)
    prop = make_property(
        public_id="hof-noTTL-0001", first_seen=datetime.now(UTC) - timedelta(days=400)
    )
    _attach(session, prop, source)
    still_here = make_property(public_id="hof-noTTL-0002")
    still_here_ps = _attach(session, still_here, source, url="https://denkmalboerse.example/x")

    mark_missing(
        session, {still_here_ps.property_id}, source=source, enumeration_complete=True
    )

    assert prop.listing_status == ListingStatus.REMOVED


def test_expired_listings_do_not_trip_the_implausible_absence_guard(
    session, make_source, make_property
) -> None:
    source = make_source(key="ovbimmo", role=SourceRole.LOCAL, listing_ttl_days=_OVB_TTL_DAYS)
    for i in range(10):
        prop = make_property(
            public_id=f"hof-ttl-{i:04d}",
            first_seen=datetime.now(UTC) - timedelta(days=20),
        )
        _attach(session, prop, source, url=f"https://ovbimmo.example/{i}")

    # All ten aged out together: normal for a fortnightly ad cycle, and must not
    # be mistaken for the parser failure the guard exists to catch. This must
    # succeed despite mark_missing's empty-seen-set guard being unconditional -
    # the guard only engages once a genuine (non-expired) absence remains.
    mark_missing(session, set(), source=source, enumeration_complete=True)
