#!/usr/bin/env python3
"""Back up the live SQLite database before a migration touches it.

`render_as_batch` makes Alembic fake an ALTER on SQLite by rebuilding the
whole table, and a rebuild that fails partway through is not something to
discover by re-fetching listings from sources that may have gone offline
since. A consistent snapshot taken with sqlite3's own backup API (safe even
with WAL active) is cheap insurance every migration should take first.
"""

from __future__ import annotations

import datetime
import os
import pathlib
import sqlite3


def main() -> None:
    url = os.environ.get("HOFRADAR_DATABASE_URL", "")
    if url and not url.startswith("sqlite"):
        raise SystemExit("Non-SQLite DSN: take a server-side dump instead, then re-run.")
    if url:
        src = pathlib.Path(url.replace("sqlite:///", ""))
    else:
        from hofradar.db.session import DEFAULT_DB_PATH

        src = DEFAULT_DB_PATH
    if not src.exists():
        raise SystemExit(f"No database at {src} - nothing to back up, continue.")

    backups_dir = pathlib.Path("backups")
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S")
    dst = backups_dir / f"hofradar-{stamp}.db"
    with sqlite3.connect(src) as s, sqlite3.connect(dst) as d:
        s.backup(d)  # consistent copy even with WAL active
    print("backed up to", dst)


if __name__ == "__main__":
    main()
