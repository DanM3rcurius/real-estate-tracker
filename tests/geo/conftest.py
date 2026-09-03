"""Local fixtures for the geo test suite.

No root-level ``tests/conftest.py`` exists yet (owned by another workstream),
so these fixtures are scoped to ``tests/geo`` only, per the task brief.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from hofradar.config import SearchProfile
from hofradar.db import models  # noqa: F401  (registers ORM mappers with Base)
from hofradar.db.session import Base, get_engine
from hofradar.geo.ratelimit import reset_all


def make_profile(**radius_overrides: float) -> SearchProfile:
    """A ``SearchProfile`` anchored at the project's Westham origin.

    ``Center`` already defaults to Westham, so this only needs to carry
    whatever ``radius`` field a test wants to vary (typically ``air_km_max``).
    """
    return SearchProfile(radius=radius_overrides)


@pytest.fixture()
def session() -> Session:
    """A fresh, isolated in-memory SQLite session for each test."""
    engine = get_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = factory()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def _fast_rate_limits():
    """Keep the suite fast: forget rate-limiter timers between tests so
    consecutive tests never pay for a real one-second sleep just because
    they happen to run within a second of each other."""
    reset_all()
    yield
    reset_all()
