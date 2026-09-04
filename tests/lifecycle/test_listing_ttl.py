"""A newspaper ad that ran its two weeks has expired, not sold.

The distinction is the whole point. REMOVED means the property left the
market and is a real event worth reporting; EXPIRED means a billing cycle
ended and says nothing about the farmstead at all. Conflating them fills the
change feed with a fortnightly metronome and drops live listings out of the
ranking.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from hofradar.db.enums import ChangeKind, ListingStatus, SourceRole
from hofradar.db.models import PropertySource
from hofradar.lifecycle import mark_missing
from hofradar.lifecycle.absence import apply_stale_rules
from hofradar.lifecycle.ingest import ingest

#: The newspaper's ad package: two weeks online.
_OVB_TTL_DAYS = 14


def _attach(
    session, prop, source, *, url: str | None = None, first_seen: datetime | None = None
) -> PropertySource:
    """Link ``prop`` to ``source`` as a currently-visible listing.

    ``mark_missing`` reasons entirely over ``PropertySource`` rows - a bare
    ``Property`` with no source link is not "carried by" anything and can
    never be found missing. ``first_seen`` lets a test backdate *this
    source's* advert independently of ``Property.first_seen`` - the TTL ages
    from the advert, not from the property's first-ever sighting.
    """
    kwargs = {
        "property_id": prop.id,
        "source_id": source.id,
        "url": url or f"https://{source.key}.example/{prop.public_id}",
        "role": source.role,
        "last_listing_visible": True,
    }
    if first_seen is not None:
        kwargs["first_seen"] = first_seen
    ps = PropertySource(**kwargs)
    session.add(ps)
    session.flush()
    return ps


def test_a_listing_older_than_the_ttl_expires_rather_than_being_removed(
    session, make_source, make_property
) -> None:
    source = make_source(key="ovbimmo", role=SourceRole.LOCAL, listing_ttl_days=_OVB_TTL_DAYS)
    old = make_property()
    _attach(session, old, source, first_seen=datetime.now(UTC) - timedelta(days=20))
    # A second, still-listed advert. Without it this run saw *nothing*, which
    # mark_missing refuses outright before it ever classifies a row - a TTL
    # explains why one advert vanished, never why the whole run came back
    # empty. That refusal is a different test (test_absence_guards.py); this
    # one is about how a single aged-out advert is classified.
    still_here = make_property(public_id="hof-old-0002")
    still_here_ps = _attach(session, still_here, source, url="https://ovbimmo.example/still-old")

    changes = mark_missing(
        session, {still_here_ps.property_id}, source=source, enumeration_complete=True
    )

    assert old.listing_status == ListingStatus.EXPIRED
    assert all(c.kind != "removed" for c in changes)


def test_a_listing_younger_than_the_ttl_is_still_a_real_removal(
    session, make_source, make_property
) -> None:
    source = make_source(key="ovbimmo", role=SourceRole.LOCAL, listing_ttl_days=_OVB_TTL_DAYS)
    fresh = make_property(public_id="hof-fresh-0001")
    _attach(session, fresh, source, first_seen=datetime.now(UTC) - timedelta(days=3))
    # A second, still-visible listing keeps the seen-set from being empty - a
    # single vanished listing is real news, not the empty-seen-set case the
    # guard above exists to catch, which this test is not exercising.
    still_here = make_property(public_id="hof-fresh-0002")
    still_here_ps = _attach(session, still_here, source, url="https://ovbimmo.example/still-here")

    mark_missing(session, {still_here_ps.property_id}, source=source, enumeration_complete=True)

    # Gone after three days is not a billing cycle - that is the seller acting.
    assert fresh.listing_status == ListingStatus.REMOVED


def test_a_source_without_a_ttl_is_unaffected(session, make_source, make_property) -> None:
    source = make_source(key="denkmalboerse", role=SourceRole.PRIMARY, listing_ttl_days=None)
    prop = make_property(public_id="hof-noTTL-0001")
    _attach(session, prop, source, first_seen=datetime.now(UTC) - timedelta(days=400))
    still_here = make_property(public_id="hof-noTTL-0002")
    still_here_ps = _attach(session, still_here, source, url="https://denkmalboerse.example/x")

    mark_missing(session, {still_here_ps.property_id}, source=source, enumeration_complete=True)

    assert prop.listing_status == ListingStatus.REMOVED


def test_a_ttl_of_zero_is_treated_as_no_window(session, make_source, make_property) -> None:
    """``listing_ttl_days=0`` is not a "the ad expires instantly" setting -
    ``if not ttl`` in ``_absence_status`` reads it the same as ``None``, so a
    misconfigured 0 fails safe to REMOVED rather than expiring everything."""
    source = make_source(key="ovbimmo", role=SourceRole.LOCAL, listing_ttl_days=0)
    prop = make_property(public_id="hof-zero-0001")
    _attach(session, prop, source, first_seen=datetime.now(UTC) - timedelta(days=400))
    still_here = make_property(public_id="hof-zero-0002")
    still_here_ps = _attach(session, still_here, source, url="https://ovbimmo.example/z")

    mark_missing(session, {still_here_ps.property_id}, source=source, enumeration_complete=True)

    assert prop.listing_status == ListingStatus.REMOVED


def test_expired_listings_do_not_trip_the_implausible_absence_guard(
    session, make_source, make_property
) -> None:
    source = make_source(key="ovbimmo", role=SourceRole.LOCAL, listing_ttl_days=_OVB_TTL_DAYS)
    for i in range(10):
        prop = make_property(public_id=f"hof-ttl-{i:04d}")
        _attach(
            session, prop, source,
            url=f"https://ovbimmo.example/{i}",
            first_seen=datetime.now(UTC) - timedelta(days=20),
        )
    # The shape a real fortnightly cycle has: the ads that aged out are gone
    # while the rest of the inventory is still on the page. That is what tells
    # a billing cycle apart from a parser that matched nothing, and it is why
    # the seen-set here is non-empty.
    still_here = make_property(public_id="hof-ttl-still")
    still_here_ps = _attach(session, still_here, source, url="https://ovbimmo.example/still")

    # Ten of eleven aged out together: 91% of the visible inventory, far past
    # IMPLAUSIBLE_ABSENCE_FRACTION. Normal for a fortnightly ad cycle, and must
    # not be mistaken for the parser failure the fraction guard exists to
    # catch - the expiry split runs before that guard for exactly this reason.
    mark_missing(
        session, {still_here_ps.property_id}, source=source, enumeration_complete=True
    )


def test_the_ttl_clock_runs_from_the_advert_not_the_property(
    session, make_source, make_property
) -> None:
    """A farmstead the database has known for 300 days that OVB only started
    advertising yesterday must not expire after one day just because the
    *property* is old - that would misread a real, same-day withdrawal as a
    billing-cycle expiry and silently drop the news."""
    source = make_source(key="ovbimmo", role=SourceRole.LOCAL, listing_ttl_days=_OVB_TTL_DAYS)
    prop = make_property(
        public_id="hof-old-known-0001", first_seen=datetime.now(UTC) - timedelta(days=300)
    )
    _attach(session, prop, source, first_seen=datetime.now(UTC) - timedelta(days=1))
    still_here = make_property(public_id="hof-old-known-0002")
    still_here_ps = _attach(session, still_here, source, url="https://ovbimmo.example/still")

    mark_missing(session, {still_here_ps.property_id}, source=source, enumeration_complete=True)

    assert prop.listing_status == ListingStatus.REMOVED


def test_a_listing_still_confirmed_by_a_verifying_source_does_not_expire(
    session, make_source, make_property
) -> None:
    """One source's advert timing out is not news about a farmstead a
    *different* verifying source still lists live - the same rule the REMOVED
    loop already applies, and the EXPIRED branch must apply it too."""
    ovb = make_source(key="ovbimmo", role=SourceRole.LOCAL, listing_ttl_days=_OVB_TTL_DAYS)
    portal = make_source(key="portal", role=SourceRole.PRIMARY)
    prop = make_property(public_id="hof-corroborated-0001", listing_status=ListingStatus.ACTIVE)
    _attach(session, prop, ovb, first_seen=datetime.now(UTC) - timedelta(days=20))
    _attach(session, prop, portal, url="https://portal.example/still-live")
    # Keeps OVB's own run from being an empty one, which mark_missing refuses
    # outright - see test_absence_guards.py.
    still_here = make_property(public_id="hof-corroborated-0002")
    still_here_ps = _attach(session, still_here, ovb, url="https://ovbimmo.example/still-corr")

    changes = mark_missing(
        session, {still_here_ps.property_id}, source=ovb, enumeration_complete=True
    )

    assert prop.listing_status == ListingStatus.ACTIVE
    assert changes == []


def test_an_expired_listing_eventually_goes_stale(session, make_source, make_property) -> None:
    """EXPIRED is not a dead end. A property whose advert expired and was
    never re-confirmed by anybody ages out on the ordinary stale clock, the
    same way ACTIVE does - it must not sit at full availability forever, and
    it must not be GONE_STATUSES either (a sale looks identical to an expiry
    from OVB's side, and the stale clock, not a false REMOVED, is how that
    case is supposed to resolve)."""
    source = make_source(key="ovbimmo", role=SourceRole.LOCAL, listing_ttl_days=_OVB_TTL_DAYS)
    long_gone = timedelta(days=60)  # past the default 45-day stale clock
    prop = make_property(
        public_id="hof-stale-expired-0001",
        listing_status=ListingStatus.EXPIRED,
        last_seen=datetime.now(UTC) - long_gone,
    )
    _attach(session, prop, source, first_seen=datetime.now(UTC) - long_gone)

    changes = apply_stale_rules(session)

    assert prop.listing_status == ListingStatus.STALE
    assert any(c.kind == ChangeKind.STALE for c in changes)


def test_a_renewed_advert_writes_a_reactivated_row_to_the_history(
    session, make_source, make_listing
) -> None:
    """Re-ingesting a listing that expired must not be reported as FIRST_SEEN
    (invariant 2), and the reappearance must be recorded under its own name:
    invariant 2's other half says reappearance *is* REACTIVATED, and the
    append-only history is the product. EXPIRED is therefore in
    DORMANT_STATUSES. Keeping the fortnightly renewal out of the ten-entry
    digest (docs/DECISIONS.md entry 15) is a reporting decision made in
    ``hofradar.report.data``, not a reason to leave the event unwritten - see
    tests/report/test_expired_renewal_is_not_digest_churn.py."""
    source = make_source(key="ovbimmo", role=SourceRole.LOCAL, listing_ttl_days=_OVB_TTL_DAYS)
    listing = make_listing(source_key="ovbimmo", url="https://ovbimmo.example/renewing")
    anchor_listing = make_listing(source_key="ovbimmo", url="https://ovbimmo.example/anchor")

    prop, first_change = ingest(session, listing, source=source, run_id=1)
    assert first_change.kind == ChangeKind.FIRST_SEEN
    # A second, still-listed advert, so the run that expires the first one is
    # not an empty one - mark_missing refuses those outright.
    anchor, _ = ingest(session, anchor_listing, source=source, run_id=1)

    ps = session.execute(
        select(PropertySource).where(PropertySource.property_id == prop.id)
    ).scalar_one()
    ps.first_seen = datetime.now(UTC) - timedelta(days=20)
    session.flush()

    mark_missing(session, {anchor.id}, source=source, enumeration_complete=True)
    assert prop.listing_status == ListingStatus.EXPIRED

    # The ad renews: the same listing is seen again.
    prop_again, renewal_change = ingest(session, listing, source=source, run_id=2)

    assert prop_again.id == prop.id
    assert renewal_change.kind != ChangeKind.FIRST_SEEN
    assert renewal_change.kind == ChangeKind.REACTIVATED
    assert renewal_change.old_status == ListingStatus.EXPIRED
    assert renewal_change.new_status == ListingStatus.ACTIVE
    assert prop.listing_status == ListingStatus.ACTIVE

    reactivations = [
        row
        for row in prop.status_history
        if row.change_kind == ChangeKind.REACTIVATED
        and row.old_status == ListingStatus.EXPIRED
    ]
    assert len(reactivations) == 1
