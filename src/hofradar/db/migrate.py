"""Bringing an existing database up to the current schema.

Decision 13 made ``alembic upgrade head`` the schema command, but nothing ran
it: ``init_db()`` calls ``create_all()``, which creates missing *tables* and is
a no-op on a table that already exists - so a column added by a migration never
reached a database that predated it. The deployment booted with
``hofradar init-db && hofradar serve``, exited 0, and then failed every query
touching the changed table (GitHub issue #7).

This module is the missing half. It is deliberately not part of ``session.py``:
``init_db()`` stays the cheap ``create_all()`` that the test suite uses for a
throwaway in-memory database, and :func:`ensure_schema` is what a process that
opens the *real*, persistent database calls instead.

Three states have to be told apart, because a database that predates Alembic
has no ``alembic_version`` row to read:

``empty``    no tables at all - create them from the models and stamp head.
``adopted``  the tables exist but Alembic never saw them: this is a database
             created by ``create_all()`` before decision 13. Stamp it at the
             revision whose schema it already has, then upgrade normally.
``tracked``  ``alembic_version`` is present - a plain upgrade.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, inspect, text
from sqlalchemy.exc import SQLAlchemyError

from hofradar.db.models import Base

log = logging.getLogger(__name__)

#: Shipped inside the package, not left at the repository root, so that an
#: installed wheel - which is all the container has - can migrate itself.
MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

#: The first revision. A pre-Alembic database is stamped here before upgrading:
#: it was created by ``create_all()`` from the models as they stood at that
#: revision, which is exactly what the baseline records.
BASELINE_REVISION = "a78c19944d2d"

#: Cheapest positive proof that a database is not empty. Every deployment has
#: this table; ``create_all()`` and the baseline migration both make it.
SENTINEL_TABLE = "properties"

ALEMBIC_VERSION_TABLE = "alembic_version"

#: The web app and the scheduler are separate containers sharing one database,
#: and `docker compose up` starts them together, so both call `ensure_schema`
#: at once. Reading the current revision, deciding, and acting happen on
#: different connections, so without a lock two processes interleave: both read
#: "not stamped", both stamp, and the loser dies with "table alembic_version
#: already exists" or "duplicate column name" - a crash-looping web container
#: while the scheduler quietly wins. Measured, not theorised.
#:
#: So one process migrates at a time and the rest wait, then find the work
#: done. The retries below stay as a second line for a dialect with no lock:
#: a migration another process already performed is not an error, it is the
#: desired end state.
_MIGRATION_ATTEMPTS = 5
_RETRY_BACKOFF_SECONDS = 0.25

#: Arbitrary but fixed: the key every Hofradar process uses for the Postgres
#: advisory lock, so they all contend on the same one.
_ADVISORY_LOCK_KEY = 0x484F4652  # "HOFR"


class SchemaError(RuntimeError):
    """The database cannot be brought to the current schema automatically."""


@dataclass(frozen=True, slots=True)
class SchemaState:
    """What :func:`ensure_schema` found and what it did about it."""

    action: str  # "created" | "adopted" | "upgraded" | "current"
    from_revision: str | None
    to_revision: str | None

    @property
    def changed(self) -> bool:
        return self.from_revision != self.to_revision


def alembic_config(connection: Connection | None = None) -> Config:
    """An Alembic config pointing at the packaged migrations.

    Passing ``connection`` binds the run to exactly that database. Without it
    ``env.py`` falls back to ``database_url()``, which is what a human running
    ``alembic upgrade head`` from a checkout wants.
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


def head_revision() -> str | None:
    """The newest revision on disk."""
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def current_revision(engine: Engine) -> str | None:
    """The revision this database is stamped at, or ``None`` if unstamped."""
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def _database_is_empty(engine: Engine) -> bool:
    return SENTINEL_TABLE not in inspect(engine).get_table_names()


def _is_stamped(engine: Engine) -> bool:
    return ALEMBIC_VERSION_TABLE in inspect(engine).get_table_names()


def _adoption_revision(engine: Engine) -> str:
    """Which revision an unstamped, already-populated database really is at.

    A database created by ``create_all()`` matches the baseline. One created by
    a *newer* ``create_all()`` - after the models grew a column but before this
    module existed - already has that column, and replaying the migration that
    adds it would fail. Stamping head is right in that case, so ask the schema
    rather than assuming.
    """
    if schema_drift(engine):
        return BASELINE_REVISION
    head = head_revision()
    if head is None:  # pragma: no cover - only with no migrations on disk
        raise SchemaError("no migrations found in " + str(MIGRATIONS_DIR))
    return head


def schema_drift(engine: Engine) -> list[str]:
    """Differences between the live database and the ORM models, as text.

    Uses Alembic's own autogenerate comparison, so this reports exactly what a
    ``revision --autogenerate`` would want to write. Empty means the database
    matches the models. Used both to decide how to adopt an unstamped database
    and to verify that an upgrade actually landed.
    """
    with engine.connect() as connection:
        from alembic.autogenerate import compare_metadata

        context = MigrationContext.configure(connection)
        return [repr(diff) for diff in compare_metadata(context, Base.metadata)]


def ensure_schema(engine: Engine | None = None) -> SchemaState:
    """Bring the database to the current schema. Safe to call on every boot.

    Safe to call from several processes at once, too - see
    ``_MIGRATION_ATTEMPTS``. Returns what it did. Raises :class:`SchemaError`
    if the database is left not matching the models, because booting into a
    half-migrated database is the failure this whole module exists to prevent -
    a stale column surfaces much later, as an ``OperationalError`` inside an
    unrelated page.
    """
    from hofradar.db.session import get_engine

    engine = engine or get_engine()
    last_error: BaseException | None = None

    for attempt in range(_MIGRATION_ATTEMPTS):
        try:
            with _migration_lock(engine):
                # Re-read inside the lock: the process we just waited for has
                # very likely done the whole job already.
                if _matches_models(engine):
                    revision = current_revision(engine)
                    return SchemaState(
                        action="current", from_revision=revision, to_revision=revision
                    )
                return _ensure_once(engine)
        except (SQLAlchemyError, SchemaError) as exc:
            last_error = exc
            if _matches_models(engine):
                # Another process migrated it while we were trying. That is a
                # success - it is the state we wanted - so say so rather than
                # taking the container down over a race we already won.
                revision = current_revision(engine)
                log.info("schema: already brought to %s by another process", revision)
                return SchemaState(action="current", from_revision=revision, to_revision=revision)
            if attempt + 1 < _MIGRATION_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))

    raise SchemaError(
        f"could not bring the database to the current schema after "
        f"{_MIGRATION_ATTEMPTS} attempts: {last_error!r}"
    ) from last_error


@contextmanager
def _migration_lock(engine: Engine) -> Iterator[None]:
    """Hold the right to migrate this database, or wait for whoever has it.

    SQLite gets an OS file lock beside the database file - the processes that
    share it also share the volume it lives on, so ``flock`` is exactly the
    right scope. Postgres gets a session advisory lock. Anything else proceeds
    unlocked and relies on the retry loop.
    """
    dialect = engine.dialect.name

    if dialect == "sqlite":
        path = engine.url.database
        if path and path != ":memory:":
            try:
                import fcntl
            except ImportError:  # pragma: no cover - not POSIX
                yield
                return
            lock_path = Path(f"{path}.migrate-lock")
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            with open(lock_path, "w") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
            return

    elif dialect == "postgresql":
        with engine.connect() as connection:
            connection.execute(
                text("SELECT pg_advisory_lock(:key)"), {"key": _ADVISORY_LOCK_KEY}
            )
            connection.commit()
            try:
                yield
            finally:
                connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": _ADVISORY_LOCK_KEY}
                )
                connection.commit()
        return

    yield


def _matches_models(engine: Engine) -> bool:
    """Is this database at head *and* free of drift, whoever got it there?"""
    try:
        return current_revision(engine) == head_revision() and not schema_drift(engine)
    except SQLAlchemyError:
        return False


def _ensure_once(engine: Engine) -> SchemaState:
    head = head_revision()

    if _database_is_empty(engine):
        Base.metadata.create_all(engine)
        _run(engine, command.stamp, "head")
        log.info("schema: new database created at %s", head)
        return SchemaState(action="created", from_revision=None, to_revision=head)

    adopted = False
    if not _is_stamped(engine):
        revision = _adoption_revision(engine)
        _run(engine, command.stamp, revision)
        log.info("schema: adopted pre-Alembic database at %s", revision)
        adopted = True

    before = current_revision(engine)
    if before != head:
        log.info("schema: upgrading %s -> %s", before, head)
        _run(engine, command.upgrade, "head")

    after = current_revision(engine)
    _verify(engine)
    if after == before and not adopted:
        return SchemaState(action="current", from_revision=before, to_revision=after)
    return SchemaState(
        action="adopted" if adopted else "upgraded", from_revision=before, to_revision=after
    )


def _run(engine: Engine, action: Any, revision: str) -> None:
    """Run one Alembic command against this engine and nothing else."""
    with engine.begin() as connection:
        action(alembic_config(connection), revision)


def _verify(engine: Engine) -> None:
    drift = schema_drift(engine)
    if drift:
        raise SchemaError(
            "database does not match the models after migrating: "
            + "; ".join(drift)
            + ". Back the database up (scripts/backup_db.py) and inspect it before "
            "running anything that writes."
        )
