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
* a *complete* enumeration can still be lying, and :class:`ImplausibleAbsence`
  is the guard against that: a run that saw nothing from a source that used to
  carry a real inventory, or that would remove an implausibly large slice of
  it in one pass, is refused outright rather than written to the append-only
  history where the fiction can never be told apart from the truth later.
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

#: A source holding at least this many listings must clear this floor before
#: the *fraction* guard below is allowed to reason about it at all. Below it,
#: a percentage is meaningless (a broker's last listing selling is 100%). It
#: has nothing to do with the *empty*-result guard below, which is
#: unconditional and does not reference this constant at all: seeing
#: literally nothing is not a percentage, it is the signature of a parser
#: that produced no output, and that is exactly as suspicious for a 1-2
#: listing source as for a much larger one.
FRACTION_GUARD_MIN_ROWS = 3

#: A run that saw this fraction or more of a source's visible inventory
#: disappear is treated as a parser failure, not as a market event. Half an
#: inventory never goes in one week; a changed HTML template does. Only
#: applied once a source clears FRACTION_GUARD_MIN_ROWS.
IMPLAUSIBLE_ABSENCE_FRACTION = 0.30

#: ... and only once at least this many listings would actually be removed.
#: A single disappearance is never evidence of a broken parser - it is the
#: single most common real event this whole module exists to record (a
#: source with exactly 3 listings loses one: that is 33%, over the fraction
#: above, and would otherwise deadlock the source into refusing forever).
#: Two or more vanishing in the same run is what engages the percentage.
IMPLAUSIBLE_ABSENCE_MIN_MISSING = 2


class ImplausibleAbsence(RuntimeError):
    """A run's absences are too broad to be believed, so nothing is written."""


#: Default patience before an unseen ACTIVE property is called STALE.
DEFAULT_STALE_AFTER_DAYS = 45

#: Patience for a property no source will ever re-report. Longer, because
#: there was never a stream to fall silent - only a human who has not been
#: back to look.
DEFAULT_UNVERIFIED_STALE_AFTER_DAYS = 180


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

    Raises :class:`ImplausibleAbsence` - and writes nothing - when the run's
    absences are too broad to be believed: an empty seen-set against a real
    inventory (unconditionally - even one visible listing), or a run that
    would remove both :data:`IMPLAUSIBLE_ABSENCE_MIN_MISSING` or more
    listings and :data:`IMPLAUSIBLE_ABSENCE_FRACTION` or more of the source's
    inventory in one pass.
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
    # enumeration looks complete. This guard is deliberately unconditional on
    # inventory size: seeing literally nothing is not a percentage, it is the
    # exact signature of a parser matching nothing, and that is exactly as
    # likely for a source with 1-2 listings as for one with a hundred - a
    # narrow search DNA makes 1-2 matches the *modal* inventory for a small
    # source, not an edge case to special-case away. The costs are asymmetric
    # (a missed removal is a stale row; a wrong removal is lost history), so
    # we refuse to write anything and make the operator look instead - a
    # returned [] here reads as "a quiet run", which is exactly the fiction
    # that must not survive.
    if rows and not seen:
        raise ImplausibleAbsence(
            f"source {source.key!r} saw nothing while {len(rows)} of its listings "
            "are still marked visible; refusing to mark them removed"
        )

    # The fraction guard is different in kind: a *partial* result is not
    # inherently suspicious the way a total-zero one is, so it only engages
    # once there is enough inventory for a percentage to mean anything
    # (FRACTION_GUARD_MIN_ROWS) AND enough would actually be removed for that
    # to be more than a single ordinary sale (IMPLAUSIBLE_ABSENCE_MIN_MISSING).
    # Skipping single removals here is what keeps a small source from
    # deadlocking: without the floor, a 3-listing source losing exactly one
    # (33%) would raise every run forever, since a refused run writes nothing
    # and the next run sees the same "still visible" row again.
    missing = [ps for ps in rows if ps.property_id not in seen]
    if len(rows) >= FRACTION_GUARD_MIN_ROWS and len(missing) >= IMPLAUSIBLE_ABSENCE_MIN_MISSING:
        fraction = len(missing) / len(rows)
        if fraction >= IMPLAUSIBLE_ABSENCE_FRACTION:
            raise ImplausibleAbsence(
                f"source {source.key!r} would remove {len(missing)} of {len(rows)} "
                f"listings ({fraction:.0%}); refusing above "
                f"{IMPLAUSIBLE_ABSENCE_FRACTION:.0%}"
            )

    changes: list[ChangeResult] = []
    for ps in missing:
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
    unverified_stale_after_days: int = DEFAULT_UNVERIFIED_STALE_AFTER_DAYS,
    non_reporting_source_ids: set[int] | None = None,
    run_id: int | None = None,
) -> list[ChangeResult]:
    """Age out properties nobody has mentioned for a while.

    STALE, never REMOVED: we did not prove the farm is gone, we merely stopped
    hearing about it, and the difference matters to every downstream score.

    Two clocks, because "we stopped hearing" only means something where there
    was a stream to fall silent:

    * a property carried by a source that re-reports every run (a portal, a
      feed, the ZVG register) is stale ``stale_after_days`` after that source
      last mentioned it - the source kept talking and stopped naming this one;
    * a property carried only by sources that never re-report - the paste box,
      a one-shot CSV, a bulletin archive - was never going to be mentioned
      again by anybody. Ageing it on the same clock says "we stopped hearing"
      about something we were never listening to. It still ages out, because
      an unconfirmed six-month-old listing *is* stale information, but on the
      longer ``unverified_stale_after_days`` clock and with a detail line that
      says what actually happened: nobody has re-checked it.

    ``non_reporting_source_ids`` names the sources in the second class. The
    caller works it out from the adapters (``SourceAdapter.enumerates``);
    passing nothing keeps every source on the short clock.
    """
    now = utcnow()
    non_reporting = set(non_reporting_source_ids or ())
    # Query on the SHORTER of the two clocks so every candidate is caught, then
    # apply the one that actually governs each property. Using the longer clock
    # here would silently skip self-reporting properties in the gap between the
    # two. One query, no per-property round trip.
    shortest = min(stale_after_days, unverified_stale_after_days) if non_reporting else stale_after_days
    props = session.execute(
        select(Property).where(
            Property.listing_status.in_(sorted(_rules.STALE_ELIGIBLE_STATUSES)),
            Property.last_seen < now - timedelta(days=shortest),
            Property.merged_into_id.is_(None),
        )
    ).scalars().all()

    # One query for the whole candidate set rather than a lookup per property.
    reporting_by_property = _reporting_sources(session, [p.id for p in props], non_reporting)

    changes: list[ChangeResult] = []
    for prop in props:
        self_reporting = reporting_by_property.get(prop.id, True)
        days = stale_after_days if self_reporting else unverified_stale_after_days
        if prop.last_seen >= now - timedelta(days=days):
            continue
        if self_reporting:
            detail = f"not seen since {prop.last_seen:%Y-%m-%d} (> {days} d)"
        else:
            detail = (
                f"no source re-checks this listing; unconfirmed since "
                f"{prop.last_seen:%Y-%m-%d} (> {days} d)"
            )
        changes.append(
            _transition(
                session, prop, ListingStatus.STALE, ChangeKind.STALE,
                detail=detail, run_id=run_id, now=now,
            )
        )
    session.flush()
    return changes


def _reporting_sources(
    session: Session, property_ids: list[int], non_reporting: set[int]
) -> dict[int, bool]:
    """property_id -> does at least one source still re-report this listing?

    A property with no sources at all counts as not self-reporting: nothing is
    going to mention it again either.
    """
    if not property_ids:
        return {}
    rows = session.execute(
        select(PropertySource.property_id, PropertySource.source_id).where(
            PropertySource.property_id.in_(property_ids)
        )
    ).all()
    out: dict[int, bool] = {pid: False for pid in property_ids}
    for property_id, source_id in rows:
        if source_id not in non_reporting:
            out[property_id] = True
    return out


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
