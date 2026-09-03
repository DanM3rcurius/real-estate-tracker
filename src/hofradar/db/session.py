"""Engine / session plumbing.

Defaults to a file-backed SQLite database so the whole app runs with zero
infrastructure. Set ``HOFRADAR_DATABASE_URL`` to a Postgres DSN for a real
deployment - the models are written to work on both.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DEFAULT_DB_PATH = Path(os.environ.get("HOFRADAR_DATA_DIR", "data")) / "hofradar.sqlite3"


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def database_url() -> str:
    url = os.environ.get("HOFRADAR_DATABASE_URL")
    if url:
        return url
    DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{DEFAULT_DB_PATH}"


def get_engine(url: str | None = None, *, echo: bool = False) -> Engine:
    global _engine, _SessionFactory
    if url is not None:
        engine = create_engine(url, echo=echo, future=True)
        _apply_sqlite_pragmas(engine)
        return engine
    if _engine is None:
        _engine = create_engine(database_url(), echo=echo, future=True)
        _apply_sqlite_pragmas(_engine)
        _SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def _apply_sqlite_pragmas(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn, _record):  # pragma: no cover - driver level
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()


def get_session() -> Session:
    if _SessionFactory is None:
        get_engine()
    assert _SessionFactory is not None
    return _SessionFactory()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(engine: Engine | None = None) -> Engine:
    """Create all tables. Alembic owns migrations; this is for tests and first boot."""
    from hofradar.db import models  # noqa: F401  (registers mappers)

    engine = engine or get_engine()
    Base.metadata.create_all(engine)
    return engine
