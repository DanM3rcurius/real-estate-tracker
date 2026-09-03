"""Fixtures for the scoring and cost-model tests.

A local, in-memory SQLite database. If a project-wide ``tests/conftest.py``
later provides a ``session`` fixture, this one simply shadows it for this
package and can be deleted.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from hofradar.db.session import Base
from hofradar.db import models  # noqa: F401  (registers the mappers)


@pytest.fixture()
def engine() -> Iterator[Engine]:
    eng = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(eng)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture()
def session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as db:
        yield db


@pytest.fixture()
def now() -> datetime:
    """A fixed clock, so freshness bands are deterministic."""
    return datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
