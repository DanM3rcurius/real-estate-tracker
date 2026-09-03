"""Shared test fixtures for the whole hofradar test suite.

Every test gets its own throwaway in-memory SQLite database, created straight
from the ORM metadata via ``init_db(create_engine("sqlite://"))``. No file, no
migration, no shared state between tests.

The factories are deliberately *general* - other packages' tests are expected
to reuse them, so they take keyword overrides for everything and never bake in
assumptions that only dedupe or lifecycle care about.

Fixtures
--------
``engine``        a fresh in-memory engine with all tables created
``db_session``    a SQLAlchemy ``Session`` bound to it (alias: ``session``)
``make_source``   ``(key=None, *, role=..., reliability=..., **kw) -> Source``
``make_property`` ``(**kw) -> Property``          (persisted and flushed)
``make_listing``  ``(**kw) -> NormalizedListing`` (plain dataclass, not stored)
``make_geo``      ``(lat, lon, *, precision="exact", **kw) -> GeoResult``

All factories that write to the database flush, so the returned object has an
``id``. None of them commit - the test owns the transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from itertools import count
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from hofradar.contracts import GeoResult, NormalizedListing
from hofradar.db.enums import ListingStatus, SourceRole, VerificationStatus
from hofradar.db.models import Property, Source
from hofradar.db.session import init_db


@pytest.fixture
def engine() -> Iterator[Engine]:
    """A fresh, empty, in-memory database per test."""
    eng = init_db(create_engine("sqlite://"))
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    """A session on the throwaway database. Rolled back and closed afterwards."""
    with Session(engine, expire_on_commit=False, future=True) as session:
        yield session
        session.rollback()


@pytest.fixture
def session(db_session: Session) -> Session:
    """Alias for :func:`db_session`, for tests that read better that way."""
    return db_session


# --------------------------------------------------------------------------- #
# Factories
# --------------------------------------------------------------------------- #


@pytest.fixture
def make_source(db_session: Session) -> Callable[..., Source]:
    """Create a ``Source``.

    Defaults to a *primary* (verifying) source with high reliability, because
    that is the common case in lifecycle tests. Pass ``role=SourceRole.DISCOVERY``
    for an aggregator that is allowed to find but not to prove.
    """
    counter = count(1)

    def _make(key: str | None = None, **kwargs: Any) -> Source:
        n = next(counter)
        key = key or f"source-{n}"
        source = Source(
            key=key,
            name=kwargs.pop("name", key.replace("-", " ").title()),
            role=kwargs.pop("role", SourceRole.PRIMARY),
            reliability=kwargs.pop("reliability", 0.8),
            base_url=kwargs.pop("base_url", f"https://{key}.example"),
            enabled=kwargs.pop("enabled", True),
            config=kwargs.pop("config", {}),
            **kwargs,
        )
        db_session.add(source)
        db_session.flush()
        return source

    return _make


@pytest.fixture
def make_property(db_session: Session) -> Callable[..., Property]:
    """Create a persisted ``Property`` with sane, overridable defaults.

    Only for tests that need a pre-existing row. Production code must never
    build a Property directly - ``hofradar.lifecycle.ingest`` owns that.
    """
    counter = count(1)

    def _make(**kwargs: Any) -> Property:
        n = next(counter)
        now = kwargs.pop("now", datetime.now(UTC))
        defaults: dict[str, Any] = {
            "public_id": f"hof-test-{n:04d}",
            "canonical_title": "Hofstelle mit Nebengebaeuden",
            "town": "Vogtareuth",
            "postcode": "83569",
            "property_type": "hofstelle",
            "listing_status": ListingStatus.ACTIVE,
            "verification_status": VerificationStatus.VERIFIED,
            "first_seen": now,
            "last_seen": now,
            "evidence": {},
            "building_features": [],
            "outbuildings": [],
            "special_features": [],
            "exclusion_flags": [],
            "llm_risks": [],
        }
        defaults.update(kwargs)
        prop = Property(**defaults)
        db_session.add(prop)
        db_session.flush()
        return prop

    return _make


@pytest.fixture
def make_listing() -> Callable[..., NormalizedListing]:
    """Build a ``NormalizedListing``. Pure dataclass - nothing is persisted."""
    counter = count(1)

    def _make(**kwargs: Any) -> NormalizedListing:
        n = next(counter)
        defaults: dict[str, Any] = {
            "source_key": "source-1",
            "url": f"https://example.test/objekt/{n}",
            "title": "Hofstelle in Vogtareuth",
            "description": "Landwirtschaftliches Anwesen mit Stadel und Obstwiese.",
            "town": "Vogtareuth",
            "postcode": "83569",
            "property_type": "hofstelle",
            "listing_visible": True,
        }
        defaults.update(kwargs)
        return NormalizedListing(**defaults)

    return _make


@pytest.fixture
def make_geo() -> Callable[..., GeoResult]:
    """Build a ``GeoResult``. ``precision`` matters: only 'exact'/'street' are
    treated as evidence of identity by the dedupe rules, because a town
    centroid is shared by every farm in the village."""

    def _make(lat: float, lon: float, **kwargs: Any) -> GeoResult:
        defaults: dict[str, Any] = {
            "lat": lat,
            "lon": lon,
            "precision": "exact",
            "provider": "test",
        }
        defaults.update(kwargs)
        return GeoResult(**defaults)

    return _make
