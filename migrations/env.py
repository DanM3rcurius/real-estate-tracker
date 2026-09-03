"""Alembic environment.

The DSN comes from ``hofradar.db.session.database_url()`` - the same function
the application itself calls - so a migration can never be applied to a
different database than the one the app opens.
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
        connectable = engine_from_config(
            config.get_section(config.config_ini_section, {}),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        with connectable.connect() as connection:
            # render_as_batch: SQLite cannot ALTER a column in place, so
            # Alembic rebuilds the table. Without it every later ALTER fails
            # on SQLite.
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
