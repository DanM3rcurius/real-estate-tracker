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
    mark_missing(session, seen_property_ids, *, source, run_id=None) -> list[ChangeResult]
    apply_stale_rules(session, *, stale_after_days=45, run_id=None) -> list[ChangeResult]
    changes_since(session, since, *, kinds=None) -> list[dict]
"""

from __future__ import annotations

from hofradar.lifecycle.absence import apply_stale_rules, mark_missing
from hofradar.lifecycle.changes import changes_since
from hofradar.lifecycle.ingest import ingest

__all__ = ["apply_stale_rules", "changes_since", "ingest", "mark_missing"]
