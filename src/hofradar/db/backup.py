"""A snapshot taken before anything destructive touches the database.

Decision 13 put this in front of every migration, and it belongs in front of
every delete for the same reason: the append-only ``observations`` history that
decision 2 calls the product cannot be refetched from sources that may have
gone offline since. ``scripts/backup_db.py`` was that snapshot, but a script in
the repository root is unreachable from an installed wheel and unusable from a
request handler, so the logic lives here and the script wraps it.

Nothing here guesses. A database this module cannot copy raises
:class:`BackupUnavailable` rather than returning ``None``, because a caller that
reads "no backup" as "backed up" is precisely the silent success this codebase
keeps producing.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

#: Where snapshots land, relative to the working directory (as the script did).
BACKUP_DIR_NAME = "backups"
BACKUP_STAMP_FORMAT = "%Y%m%dT%H%M%S"
BACKUP_PREFIX = "hofradar-"
BACKUP_SUFFIX = ".db"

#: SQLite's own name for a database that has no file behind it.
MEMORY_DATABASE = ":memory:"


class BackupUnavailable(RuntimeError):
    """No snapshot could be taken, and the caller must not proceed as if one was."""


def backup_database(url: str | None = None, *, into: Path | None = None) -> Path | None:
    """Copy the SQLite database aside and return the snapshot's path.

    ``None`` means there was nothing to copy - an in-memory database, or a file
    that does not exist yet. A database that exists but cannot be snapshotted
    here (a Postgres DSN, where the dump is the operator's job) raises
    :class:`BackupUnavailable`.
    """
    from hofradar.db.session import database_url

    parsed = make_url(url or database_url())
    if parsed.get_backend_name() != "sqlite":
        raise BackupUnavailable(
            f"{parsed.get_backend_name()} database: take a server-side dump first"
        )
    if not parsed.database or parsed.database == MEMORY_DATABASE:
        return None
    source = Path(parsed.database)
    if not source.exists():
        return None

    target_dir = into or Path(BACKUP_DIR_NAME)
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime(BACKUP_STAMP_FORMAT)
    destination = target_dir / f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)  # consistent copy even with WAL active
    return destination
