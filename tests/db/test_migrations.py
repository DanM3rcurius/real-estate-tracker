"""The schema a running deployment actually gets.

Two things are tested here, and the second is the one that was missing when
GitHub issue #7 shipped: not only that the migration *environment* is wired to
the right metadata, but that some code path actually *runs* it against a
database that already exists. ``create_all()`` is a no-op on an existing table,
so a suite that only ever builds a database from the models cannot see a
missing migration - every test passes and the deployment fails on boot.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from hofradar.db.migrate import (
    BASELINE_REVISION,
    MIGRATIONS_DIR,
    SchemaError,
    alembic_config,
    current_revision,
    ensure_schema,
    head_revision,
    schema_drift,
)
from hofradar.db.models import Base
from hofradar.db.session import init_db


def _engine(tmp_path: Path, name: str = "db.sqlite3"):
    return create_engine(f"sqlite:///{tmp_path / name}")


def _columns(tmp_path: Path, table: str, name: str = "db.sqlite3") -> list[str]:
    connection = sqlite3.connect(tmp_path / name)
    try:
        return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
    finally:
        connection.close()


def _at_baseline(tmp_path: Path, name: str = "db.sqlite3"):
    """A database as it stood before the newest migration."""
    engine = _engine(tmp_path, name)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), BASELINE_REVISION)
    return engine


# --------------------------------------------------------------------------
# the environment
# --------------------------------------------------------------------------


def test_alembic_env_target_metadata_covers_models() -> None:
    config = Config("alembic.ini")
    assert config.get_main_option("script_location") == "src/hofradar/migrations"

    from hofradar.migrations.env import target_metadata

    assert target_metadata is Base.metadata
    assert "properties" in target_metadata.tables
    assert "observations" in target_metadata.tables


def test_migrations_ship_inside_the_package() -> None:
    """The container installs a wheel and never sees the repository root.

    Leaving the migrations beside ``alembic.ini`` at the top of the checkout
    meant the deployed image had no way to migrate itself even by hand.
    """
    assert MIGRATIONS_DIR.is_dir()
    assert MIGRATIONS_DIR == Path(__file__).resolve().parents[2] / "src/hofradar/migrations"
    assert (MIGRATIONS_DIR / "env.py").is_file()
    assert list((MIGRATIONS_DIR / "versions").glob("*.py"))


def test_head_is_reachable_without_a_working_directory() -> None:
    assert head_revision() is not None


# --------------------------------------------------------------------------
# ensure_schema across the states a real volume can be in
# --------------------------------------------------------------------------


def test_empty_database_is_created_and_stamped(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    state = ensure_schema(engine)

    assert state.action == "created"
    assert state.to_revision == head_revision()
    assert current_revision(engine) == head_revision()
    assert "properties" in inspect(engine).get_table_names()


def test_a_database_one_migration_behind_is_upgraded(tmp_path: Path) -> None:
    """The exact shape of issue #7."""
    engine = _at_baseline(tmp_path)
    assert "listing_ttl_days" not in _columns(tmp_path, "sources")

    state = ensure_schema(engine)

    assert state.action == "upgraded"
    assert state.from_revision == BASELINE_REVISION
    assert state.to_revision == head_revision()
    assert "listing_ttl_days" in _columns(tmp_path, "sources")


def test_pre_alembic_database_is_adopted_then_upgraded(tmp_path: Path) -> None:
    """A volume written by ``create_all()`` before Alembic existed.

    It has the tables but no ``alembic_version`` to read, so a plain upgrade
    would try to create tables that are already there.
    """
    engine = _at_baseline(tmp_path)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))

    state = ensure_schema(engine)

    assert state.action == "adopted"
    assert state.to_revision == head_revision()
    assert "listing_ttl_days" in _columns(tmp_path, "sources")


def test_unstamped_but_current_database_is_adopted_at_head(tmp_path: Path) -> None:
    """``create_all()`` from today's models, never stamped.

    The column is already there, so replaying the migration that adds it would
    fail. Adoption has to read the schema rather than assume the baseline.
    """
    engine = _engine(tmp_path)
    init_db(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS alembic_version"))

    state = ensure_schema(engine)

    assert state.to_revision == head_revision()
    assert not schema_drift(engine)


def test_ensure_schema_is_idempotent(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    ensure_schema(engine)
    state = ensure_schema(engine)

    assert state.action == "current"
    assert not state.changed


def test_ensure_schema_touches_only_the_database_it_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``env.py`` used to override the DSN unconditionally.

    A caller that passed an engine explicitly then migrated whatever
    ``HOFRADAR_DATABASE_URL`` named - a database nobody was looking at.
    """
    bystander = tmp_path / "bystander.sqlite3"
    monkeypatch.setenv("HOFRADAR_DATABASE_URL", f"sqlite:///{bystander}")

    ensure_schema(_at_baseline(tmp_path, "target.sqlite3"))

    assert "listing_ttl_days" in _columns(tmp_path, "sources", "target.sqlite3")
    assert not bystander.exists()


def test_a_database_that_still_drifts_is_refused(tmp_path: Path) -> None:
    """Booting half-migrated is the failure this module exists to prevent."""
    engine = _engine(tmp_path)
    ensure_schema(engine)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE sources DROP COLUMN listing_ttl_days"))

    with pytest.raises(SchemaError, match="does not match the models"):
        ensure_schema(engine)


def test_schema_drift_is_empty_for_a_migrated_database(tmp_path: Path) -> None:
    """Guards the drift check itself against false positives.

    A check that cried wolf on every boot would be turned off within a week.
    """
    engine = _engine(tmp_path)
    ensure_schema(engine)
    assert schema_drift(engine) == []


def test_drift_names_the_missing_column(tmp_path: Path) -> None:
    engine = _at_baseline(tmp_path)
    assert any("listing_ttl_days" in item for item in schema_drift(engine))


def test_migrations_alone_reproduce_the_models(tmp_path: Path) -> None:
    """A model change with no migration behind it fails here, not in production.

    The suite builds its databases with ``create_all()``, so a column added to
    ``models.py`` and never migrated is invisible to every other test - and
    then missing from the one database that matters. Building from the
    migrations *only* and comparing against the models closes that gap.
    """
    engine = _engine(tmp_path, "from_migrations.sqlite3")
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "head")

    assert schema_drift(engine) == [], (
        "the models have a change with no migration - run "
        "`alembic -c alembic.ini revision --autogenerate -m '...'`"
    )
