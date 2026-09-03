"""Undoing the phantom removals of GitHub issue #2.

Fixing the bug stopped new false removals; it did not resurrect the rows that
had already been written. Reactivation only happens on re-ingest, and the
sources responsible here - the paste box, a CSV import - never re-ingest
anything. So the properties a user typed in by hand stayed REMOVED forever.

This is deliberately narrow. It reverses only the transitions it can prove
were wrong: a REMOVED written by absence detection, attributed to a source
that structurally cannot enumerate and therefore never had standing to prove
absence in the first place. A removal by a source that *can* enumerate is left
alone even though the error path could have produced it too, because from the
history alone it is indistinguishable from a real removal - and inventing a
resurrection is the same class of mistake as inventing a removal.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from hofradar.db.enums import ChangeKind, ListingStatus
from hofradar.db.models import Property, PropertySource, Source, StatusHistory, utcnow

log = logging.getLogger(__name__)

#: The exact detail string absence.mark_missing writes. The source key is the
#: capture group - it is what tells us who claimed the removal.
REMOVAL_DETAIL = re.compile(r"^no verifying source still lists it \((?P<key>[^)]+)\)$")


@dataclass(slots=True)
class RepairReport:
    restored: list[str] = field(default_factory=list)
    skipped_ambiguous: list[str] = field(default_factory=list)
    dry_run: bool = True

    @property
    def total(self) -> int:
        return len(self.restored)

    def summary(self) -> str:
        verb = "would restore" if self.dry_run else "restored"
        lines = [f"{verb} {len(self.restored)} propert{'y' if len(self.restored) == 1 else 'ies'}"]
        for public_id in self.restored:
            lines.append(f"  + {public_id}")
        if self.skipped_ambiguous:
            lines.append(
                f"left alone ({len(self.skipped_ambiguous)}): removed by a source that "
                "can enumerate, so the removal may well be real"
            )
            for public_id in self.skipped_ambiguous:
                lines.append(f"  ? {public_id}")
        return "\n".join(lines)


def repair_phantom_removals(
    session: Session,
    *,
    non_reporting_source_keys: set[str],
    dry_run: bool = True,
) -> RepairReport:
    """Restore properties removed by a source that could never prove absence.

    ``non_reporting_source_keys`` comes from the adapters
    (``SourceAdapter.enumerates`` is False). Nothing is written when
    ``dry_run`` is true, so the safe move is always to look first.
    """
    report = RepairReport(dry_run=dry_run)
    now = utcnow()

    removed = session.execute(
        select(Property).where(
            Property.listing_status == ListingStatus.REMOVED,
            Property.merged_into_id.is_(None),
        )
    ).scalars().all()

    for prop in removed:
        transition = _last_removal(prop)
        if transition is None:
            continue
        match = REMOVAL_DETAIL.match(transition.detail or "")
        if match is None:
            continue  # removed for some other reason; not ours to touch
        key = match.group("key")
        if key not in non_reporting_source_keys:
            report.skipped_ambiguous.append(prop.public_id)
            continue

        report.restored.append(prop.public_id)
        if dry_run:
            continue

        prop.listing_status = transition.old_status or ListingStatus.ACTIVE
        prop.removed_at = None
        for row in _source_rows(session, prop.id, key):
            row.last_listing_visible = True
        # Append through the relationship rather than session.add, so an
        # already-loaded prop.status_history reflects the repair immediately.
        prop.status_history.append(
            StatusHistory(
                property_id=prop.id,
                observed_at=now,
                old_status=ListingStatus.REMOVED,
                new_status=prop.listing_status,
                change_kind=ChangeKind.REACTIVATED,
                detail=(
                    f"repair: '{key}' never had standing to prove absence "
                    "(GitHub issue #2)"
                ),
            )
        )

    if not dry_run:
        session.flush()
    log.info("%s", report.summary())
    return report


def _last_removal(prop: Property) -> StatusHistory | None:
    removals = [
        h
        for h in (prop.status_history or [])
        if h.new_status == ListingStatus.REMOVED
    ]
    if not removals:
        return None
    return max(removals, key=lambda h: h.observed_at)


def _source_rows(session: Session, property_id: int, source_key: str) -> list[PropertySource]:
    return list(
        session.execute(
            select(PropertySource)
            .join(Source, Source.id == PropertySource.source_id)
            .where(PropertySource.property_id == property_id, Source.key == source_key)
        ).scalars()
    )
