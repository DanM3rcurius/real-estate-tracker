"""The history records a renewed advert; the digest does not headline it.

``EXPIRED`` is in ``DORMANT_STATUSES``, so a newspaper advert that ran out and
came back writes a ``ChangeKind.REACTIVATED`` row - invariant 2 says a
reappearance is a reactivation, and the append-only history is the product.
docs/DECISIONS.md entry 15 says the opposite thing about the *digest*: a
fortnightly billing cycle must not turn the weekly ten-entry report into a
REAKTIVIERT metronome.

Both hold, because they are different questions asked in different modules.
This pins the seam: the row exists, and ``categorise`` still calls the property
``known``. Nothing newsworthy is suppressed - an advert down long enough for
its return to mean something has been moved on to STALE by ``apply_stale_rules``
first, and a reactivation out of STALE does count, which the second test pins.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from hofradar.db.enums import ChangeKind, ListingStatus, SourceRole
from hofradar.db.models import PropertySource, StatusHistory
from hofradar.lifecycle import ingest, mark_missing
from hofradar.report.data import categorise

#: The newspaper's paid advertising window.
_OVB_TTL_DAYS = 14
#: Far enough back that the report window still contains this run's events.
_SINCE = datetime.now(UTC) - timedelta(days=7)


def _expire_then_renew(session, make_source, make_listing):
    """Ingest an advert, let its paid window run out, then re-ingest it."""
    source = make_source(key="ovbimmo", role=SourceRole.LOCAL, listing_ttl_days=_OVB_TTL_DAYS)
    listing = make_listing(source_key="ovbimmo", url="https://ovbimmo.example/renewing")
    # A second, still-listed advert: a run that saw nothing at all is refused
    # by mark_missing before it classifies anything (see
    # tests/lifecycle/test_absence_guards.py).
    anchor_listing = make_listing(source_key="ovbimmo", url="https://ovbimmo.example/anchor")

    prop, _ = ingest(session, listing, source=source, run_id=1)
    anchor, _ = ingest(session, anchor_listing, source=source, run_id=1)

    ps = session.execute(
        select(PropertySource).where(PropertySource.property_id == prop.id)
    ).scalar_one()
    ps.first_seen = datetime.now(UTC) - timedelta(days=20)
    session.flush()

    mark_missing(session, {anchor.id}, source=source, enumeration_complete=True)
    assert prop.listing_status == ListingStatus.EXPIRED

    ingest(session, listing, source=source, run_id=2)
    return prop


def test_a_renewed_advert_is_in_the_history_but_not_reported_as_reactivated(
    session, make_source, make_listing
) -> None:
    prop = _expire_then_renew(session, make_source, make_listing)

    reactivations = [
        row for row in prop.status_history if row.change_kind == ChangeKind.REACTIVATED
    ]
    assert len(reactivations) == 1
    assert reactivations[0].old_status == ListingStatus.EXPIRED

    # ...and the weekly digest still calls it what it is: a known property.
    assert categorise(prop, _SINCE) == "known"


def test_a_reactivation_out_of_stale_is_still_reported(
    session, make_source, make_listing
) -> None:
    """The other direction, so the filter above cannot quietly widen: only an
    expired *advert* is excused. A listing that went STALE and came back is
    real news about the farmstead and must still reach the digest.
    """
    prop = _expire_then_renew(session, make_source, make_listing)
    session.add(
        StatusHistory(
            property_id=prop.id,
            observed_at=datetime.now(UTC),
            old_status=ListingStatus.STALE,
            new_status=ListingStatus.ACTIVE,
            change_kind=ChangeKind.REACTIVATED,
            detail="seen again after stale",
        )
    )
    session.flush()
    session.refresh(prop)

    assert categorise(prop, _SINCE) == "reactivated"
