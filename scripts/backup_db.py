#!/usr/bin/env python3
"""Back up the live SQLite database before a migration touches it.

`render_as_batch` makes Alembic fake an ALTER on SQLite by rebuilding the
whole table, and a rebuild that fails partway through is not something to
discover by re-fetching listings from sources that may have gone offline
since. A consistent snapshot taken with sqlite3's own backup API (safe even
with WAL active) is cheap insurance every migration should take first.

The snapshot itself lives in `hofradar.db.backup`, because `hofradar
delete-property` and the dossier's danger zone need exactly the same one and
cannot shell out to a script that an installed wheel does not carry.
"""

from __future__ import annotations

import os

from hofradar.db.backup import BackupUnavailable, backup_database


def main() -> None:
    try:
        destination = backup_database(os.environ.get("HOFRADAR_DATABASE_URL") or None)
    except BackupUnavailable as exc:
        raise SystemExit(f"{exc}, then re-run.") from exc
    if destination is None:
        raise SystemExit("No database file yet - nothing to back up, continue.")
    print("backed up to", destination)


if __name__ == "__main__":
    main()
