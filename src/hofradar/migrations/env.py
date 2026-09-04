"""Alembic environment.

The DSN defaults to ``hofradar.db.session.database_url()`` - the same function
the application itself calls - so a migration run from the command line can
never be applied to a different database than the one the app opens.

A programmatic caller (``hofradar.db.migrate.ensure_schema``) is more specific
still: it puts the very engine it inspected into ``config.attributes`` and that
connection is used as-is. Overriding the DSN unconditionally, as this file once
did, meant a caller that passed a database explicitly silently migrated
whatever ``HOFRADAR_DATABASE_URL`` happened to name instead - the one mistake
in here that can damage a database nobody was even looking at.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from hofradar.db.models import Base
from hofradar.db.session import database_url

target_metadata = Base.metadata

# ``context.config`` only exists once Alembic's CLI has installed an
# EnvironmentContext around this module (see alembic/command.py). Importing
# this module directly - as tests do, to reach ``target_metadata`` without
# running a migration - leaves that attribute absent rather than set, so the
# whole run block is skipped in that case and only in that case.
config = getattr(context, "config", None)

if config is not None:
    # An explicit DSN or a passed-in connection wins; only fall back to the
    # configured database when the caller named none.
    if not config.get_main_option("sqlalchemy.url", None):
        config.set_main_option("sqlalchemy.url", database_url())

    def run_migrations_offline() -> None:
        context.configure(
            url=config.get_main_option("sqlalchemy.url"),
            target_metadata=target_metadata,
            literal_binds=True,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()

    def run_migrations_online() -> None:
        connection = config.attributes.get("connection")
        if connection is not None:
            _run(connection)
            return
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        with connectable.connect() as connection:
            _run(connection)

    def _run(connection) -> None:
        # render_as_batch: SQLite cannot ALTER a column in place, so Alembic
        # rebuilds the table. Without it every later ALTER fails on SQLite.
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()

    if context.is_offline_mode():
        run_migrations_offline()
    else:
        run_migrations_online()
