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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, Engine, inspect

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

    Returns what it did. Raises :class:`SchemaError` if the database is left
    not matching the models, because booting into a half-migrated database is
    the failure this whole module exists to prevent - a stale column surfaces
    much later, as an ``OperationalError`` inside an unrelated page.
    """
    from hofradar.db.session import get_engine

    engine = engine or get_engine()
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
