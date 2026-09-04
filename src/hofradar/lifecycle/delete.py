"""Removing a property, which the rest of this package exists to avoid.

Everything else here is built so that nothing is ever forgotten: observations
are append-only, a disappearance is a status and not a deletion, and a merge
moves rows rather than dropping them. So a delete is a deliberate, narrow act -
a mis-crawl, a duplicate that dedupe cannot see, a listing that was never a
property - and it is guarded three ways.

*It takes everything with it.* Nine tables hang off ``properties``; eight are
cascaded by the ORM's ``delete-orphan`` and ``verification_events`` has no ORM
relationship at all, so it depends on ``ON DELETE CASCADE`` actually being
enforced. SQLite enforces foreign keys only with ``PRAGMA foreign_keys=ON``,
which ``hofradar.db.session`` sets on every connection - a database opened
without it silently keeps the audit trail of a property that no longer exists.

*It refuses to resurrect a merge.* ``properties.merged_into_id`` is
``ON DELETE SET NULL``, so deleting the survivor of a merge would quietly turn
every duplicate that was merged into it back into a visible property. That is
:class:`ResurrectsMergedDuplicates`, and the answer is to delete the duplicates
first or not at all.

*It takes a snapshot first.* See :mod:`hofradar.db.backup`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import func, select

from hofradar.db.models import (
    CostEstimate,
    Document,
    Image,
    Observation,
    PriceHistory,
    Property,
    PropertySource,
    Score,
    StatusHistory,
    VerificationEvent,
)

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import Session

#: Every table that hangs off a property, in reading order. ``verification_events``
#: is last and is the one with no ORM relationship: it goes by DB cascade alone.
CHILD_MODELS: tuple[type, ...] = (
    Observation,
    PropertySource,
    PriceHistory,
    StatusHistory,
    Image,
    Document,
    Score,
    CostEstimate,
    VerificationEvent,
)


class ResurrectsMergedDuplicates(RuntimeError):
    """Another property was merged into this one; deleting it would revive them."""


@dataclass(frozen=True, slots=True)
class DeletionReport:
    """What went, so the operator sees the size of what they just did."""

    public_id: str
    title: str | None
    #: table name -> rows that went with the property
    children: dict[str, int]
    backup_path: Path | None
    dry_run: bool = False

    @property
    def total(self) -> int:
        return sum(self.children.values())

    def summary(self) -> str:
        verb = "würde gelöscht" if self.dry_run else "gelöscht"
        rows = ", ".join(f"{table}: {count}" for table, count in self.children.items() if count)
        backup = f"\nSicherung: {self.backup_path}" if self.backup_path else ""
        return (
            f"{self.public_id} „{self.title or 'ohne Titel'}“ {verb} "
            f"({self.total} abhängige Zeilen)\n{rows or 'keine abhängigen Zeilen'}{backup}"
        )


def dependent_rows(session: Session, prop: Property) -> dict[str, int]:
    """How many rows in each child table point at this property. Writes nothing."""
    counts: dict[str, int] = {}
    for model in CHILD_MODELS:
        counts[model.__tablename__] = (
            session.scalar(
                select(func.count()).select_from(model).where(model.property_id == prop.id)
            )
            or 0
        )
    return counts


def _merged_into_this(session: Session, prop: Property) -> list[str]:
    return list(
        session.scalars(
            select(Property.public_id).where(Property.merged_into_id == prop.id)
        ).all()
    )


def delete_property(session: Session, prop: Property, *, backup: bool = True) -> DeletionReport:
    """Delete one property and everything hanging off it. Commits.

    Raises :class:`ResurrectsMergedDuplicates` when another row was merged into
    this one, and :class:`~hofradar.db.backup.BackupUnavailable` when a snapshot
    was asked for and could not be taken. In both cases nothing is written.
    """
    from hofradar.db.backup import backup_database

    duplicates = _merged_into_this(session, prop)
    if duplicates:
        raise ResurrectsMergedDuplicates(
            f"{prop.public_id} ist das Ergebnis einer Zusammenführung von "
            f"{', '.join(duplicates)} - diese Duplikate würden wieder auftauchen"
        )

    children = dependent_rows(session, prop)
    public_id, title = prop.public_id, prop.canonical_title

    backup_path: Path | None = None
    if backup:
        bind = session.get_bind()
        backup_path = backup_database(str(bind.url))

    session.delete(prop)
    session.commit()
    return DeletionReport(
        public_id=public_id, title=title, children=children, backup_path=backup_path
    )
