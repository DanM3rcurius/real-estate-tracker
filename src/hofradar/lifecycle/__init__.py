"""Lifecycle: the module that makes the database remember.

Two failure modes are unacceptable and the whole package is arranged around
preventing them:

* a property that was ever in the database being reported as NEW again -
  prevented structurally in :func:`~hofradar.lifecycle.ingest.ingest`, where
  ``FIRST_SEEN`` can only be produced by the branch that just inserted the row;
* five listings of one farm becoming five properties - prevented by routing
  every write through ``hofradar.dedupe.find_duplicate`` before a row is made.

Public API (see docs/MODULE_API.md)::

    ingest(session, listing, *, run_id=None, source, geo=None) -> (Property, ChangeResult)
    mark_missing(session, seen_property_ids, *, source, run_id=None,
                 enumeration_complete) -> list[ChangeResult]
    apply_stale_rules(session, *, stale_after_days=45,
                      unverified_stale_after_days=180,
                      non_reporting_source_ids=None, run_id=None) -> list[ChangeResult]
    changes_since(session, since, *, kinds=None) -> list[dict]
    repair_phantom_removals(session, *, non_reporting_source_keys,
                            dry_run=True) -> RepairReport
    ImplausibleAbsence(RuntimeError)  # raised by mark_missing, nothing written
    delete_property(session, prop, *, backup=True) -> DeletionReport
    dependent_rows(session, prop) -> dict[str, int]
    ResurrectsMergedDuplicates(RuntimeError)  # raised by delete_property
"""

from __future__ import annotations

from hofradar.lifecycle.absence import ImplausibleAbsence, apply_stale_rules, mark_missing
from hofradar.lifecycle.changes import changes_since
from hofradar.lifecycle.delete import (
    DeletionReport,
    ResurrectsMergedDuplicates,
    delete_property,
    dependent_rows,
)
from hofradar.lifecycle.ingest import ingest
from hofradar.lifecycle.repair import RepairReport, repair_phantom_removals

__all__ = [
    "DeletionReport",
    "ImplausibleAbsence",
    "RepairReport",
    "ResurrectsMergedDuplicates",
    "apply_stale_rules",
    "changes_since",
    "delete_property",
    "dependent_rows",
    "ingest",
    "mark_missing",
    "repair_phantom_removals",
]
