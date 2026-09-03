"""What it means when a listing stops showing up.

Absence is the hardest evidence in the system to get right, because the obvious
reading is almost always wrong. A farm vanishing from an aggregator index means
the aggregator reindexed, not that the farm sold. So:

* :func:`mark_missing` only listens to sources that are allowed to prove
  things. A discovery source's silence is discarded outright - it never clears
  a visibility flag and never removes a property;
* and being *allowed* to prove things is not enough. The caller must also state
  that this source performed a **complete enumeration** on this run. Permission
  and completeness are different facts, and reading absence out of an
  incomplete result set is how a paste box, a one-shot CSV import, a truncated
  crawl or a source that simply threw a 403 end up deleting real history;
* a property is only moved to REMOVED once **no verifying source** still shows
  it. One broker page pulling the listing while another still carries it is not
  a removal;
* :func:`apply_stale_rules` exists precisely so that "we have not heard about
  this in six weeks" has somewhere to go that is *not* REMOVED. STALE means we
  stopped hearing; REMOVED means somebody checked and it was gone.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from hofradar.contracts import ChangeResult
from hofradar.db.enums import ChangeKind, ListingStatus
from hofradar.db.models import Property, PropertySource, Source, StatusHistory, utcnow
from hofradar.lifecycle import _rules

log = logging.getLogger(__name__)

#: A source holding at least this many listings that returns none at all is
#: treated as broken rather than emptied. Below it, a genuine sell-out is
#: plausible enough to believe.
EMPTY_RESULT_GUARD_MIN_ROWS = 3

#: Default patience before an unseen ACTIVE property is called STALE.
DEFAULT_STALE_AFTER_DAYS = 45


def mark_missing(
    session: Session,
    seen_property_ids: set[int],
    *,
    source: Source,
    run_id: int | None = None,
    enumeration_complete: bool,
) -> list[ChangeResult]:
    """Record that ``source`` no longer carries the properties it used to.

    ``enumeration_complete`` must be True only when this source listed its
    entire current inventory on this run, without error and without
    truncation. It is keyword-only and has no default on purpose: every caller
    has to answer the question consciously, because getting it wrong deletes
    history silently.

    Returns one :class:`ChangeResult` per property that actually transitioned
    to REMOVED. Properties that merely lost one of several sources are updated
    silently - losing a source is not news, losing the last one is.
    """
    if not _rules.can_verify(source):
        # A discovery source's silence proves nothing whatsoever.
        return []

    if not enumeration_complete:
        # We did not see everything this source has, so we know nothing about
        # what it no longer has.
        log.info(
            "%s: skipping absence detection - enumeration was not complete", source.key
        )
        return []

    now = utcnow()
    seen = set(seen_property_ids or ())
    rows = session.execute(
        select(PropertySource).where(
            PropertySource.source_id == source.id,
            PropertySource.last_listing_visible.is_(True),
        )
    ).scalars().all()

    # Last line of defence against silent parser rot. A site redesign makes
    # selectors match nothing: HTTP 200, no exception, zero results - and the
    # enumeration looks complete. A broker legitimately selling their last one
    # or two listings is plausible; a source with a real portfolio going to
    # zero in one step is far more likely to be broken. The costs are
    # asymmetric (a missed removal is a stale row; a wrong removal is lost
    # history), so above the threshold we make the operator look instead.
    if not seen and len(rows) >= EMPTY_RESULT_GUARD_MIN_ROWS:
        log.warning(
            "%s: enumeration returned nothing while %d listings were on record - "
            "refusing to read that as %d removals. Check the adapter.",
            source.key,
            len(rows),
            len(rows),
        )
        return []

    changes: list[ChangeResult] = []
    for ps in rows:
        if ps.property_id in seen:
            continue
        ps.last_listing_visible = False
        prop = session.get(Property, ps.property_id)
        if prop is None or prop.merged_into_id is not None:
            continue
        if _any_verifying_source_visible(session, prop.id):
            continue
        if prop.listing_status == ListingStatus.REMOVED:
            continue
        changes.append(_transition(session, prop, ListingStatus.REMOVED, ChangeKind.REMOVED,
                                   detail=f"no verifying source still lists it ({source.key})",
                                   run_id=run_id, now=now))
        prop.removed_at = now
    session.flush()
    return changes


def apply_stale_rules(
    session: Session,
    *,
    stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
    run_id: int | None = None,
) -> list[ChangeResult]:
    """Age out properties nobody has mentioned for a while.

    STALE, never REMOVED: we did not prove the farm is gone, we merely stopped
    hearing about it, and the difference matters to every downstream score.
    """
    now = utcnow()
    cutoff = now - timedelta(days=stale_after_days)
    props = session.execute(
        select(Property).where(
            Property.listing_status.in_(sorted(_rules.STALE_ELIGIBLE_STATUSES)),
            Property.last_seen < cutoff,
            Property.merged_into_id.is_(None),
        )
    ).scalars().all()

    changes = [
        _transition(
            session,
            prop,
            ListingStatus.STALE,
            ChangeKind.STALE,
            detail=f"not seen since {prop.last_seen:%Y-%m-%d} (> {stale_after_days} d)",
            run_id=run_id,
            now=now,
        )
        for prop in props
    ]
    session.flush()
    return changes


def _any_verifying_source_visible(session: Session, property_id: int) -> bool:
    rows = session.execute(
        select(PropertySource, Source)
        .join(Source, Source.id == PropertySource.source_id)
        .where(PropertySource.property_id == property_id)
    ).all()
    return any(src.can_verify and ps.last_listing_visible for ps, src in rows)


def _transition(
    session: Session,
    prop: Property,
    new_status: str,
    kind: str,
    *,
    detail: str,
    run_id: int | None,
    now,
) -> ChangeResult:
    old_status = prop.listing_status
    prop.listing_status = new_status
    session.add(
        StatusHistory(
            property_id=prop.id,
            observed_at=now,
            old_status=old_status,
            new_status=new_status,
            change_kind=kind,
            detail=detail,
            run_id=run_id,
        )
    )
    return ChangeResult(
        kind=kind,
        old_status=old_status,
        new_status=new_status,
        old_price=prop.price,
        new_price=prop.price,
        detail=detail,
    )
