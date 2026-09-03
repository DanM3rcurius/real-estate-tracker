"""Fixtures for the web tests.

Everything is offline and in memory: one SQLite connection shared by the whole
test (``StaticPool``), the app built through :func:`create_app` with that
factory injected, and the config directory pinned to the repository's own
``config/`` so ``profile_hash`` is stable no matter where pytest is invoked
from. Nothing here touches ``data/hofradar.sqlite3``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from hofradar.db.enums import (
    ChangeKind,
    ListingStatus,
    PriceType,
    SourceRole,
    VerificationStatus,
)
from hofradar.db.models import (
    CostEstimate,
    Observation,
    PriceHistory,
    Property,
    PropertySource,
    Score,
    Source,
    StatusHistory,
)
from hofradar.db.session import Base
from hofradar.web.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"


@pytest.fixture(autouse=True)
def _pinned_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the YAML search DNA so profile hashes do not depend on the cwd."""
    import hofradar.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", CONFIG_DIR)
    config.load_config.cache_clear()


@pytest.fixture()
def engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def session_factory(engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@pytest.fixture()
def db(session_factory) -> Iterator[Session]:
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def app(session_factory):
    return create_app(session_factory=session_factory)


@pytest.fixture()
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# Seeding helpers
# --------------------------------------------------------------------------- #


NOW = datetime(2026, 9, 3, 9, 0, tzinfo=UTC)


@pytest.fixture()
def source(db: Session) -> Source:
    row = Source(
        key="testportal",
        name="Testportal",
        role=SourceRole.PRIMARY,
        reliability=0.9,
        enabled=True,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def make_property(session: Session, **overrides: Any) -> Property:
    """A plausible farmstead that the real scoring package accepts.

    The numbers matter: ``hofradar.scoring`` rejects a property whose modelled
    total cost blows the budget and one whose confidence is below
    ``min_confidence_to_keep``, so the defaults here fill every completeness
    field and stay inside the shipped budget. A fixture that every gate rejects
    would make these tests pass by showing nothing.
    """
    defaults: dict[str, Any] = {
        "public_id": "HF-0001",
        "canonical_title": "Hofstelle mit Stadel",
        "description": "Alter Hof mit Stadel und Stall in Alleinlage.",
        "town": "Bad Feilnbach",
        "postcode": "83075",
        "lat": 47.86,
        "lon": 12.01,
        "geo_precision": "exact",
        "distance_air_km": 23.4,
        "distance_driving_km": None,
        "price": 395_000.0,
        "price_type": PriceType.ASKING,
        "price_first": 470_000.0,
        "price_reduction_count": 1,
        "land_sqm": 8_400.0,
        "living_sqm": 150.0,
        "usable_sqm": 120.0,
        "rooms": 6.0,
        "year_built": 1962,
        "condition": "fair",
        "property_type": "Hofstelle",
        "outbuildings": ["stadel"],
        "building_features": ["gewoelbekeller"],
        "special_features": ["alleinlage"],
        "exclusion_flags": [],
        "evidence": {},
        "listing_status": ListingStatus.ACTIVE,
        "verification_status": VerificationStatus.VERIFIED,
        "first_seen": NOW - timedelta(days=200),
        "last_seen": NOW,
        "last_verified": NOW,
    }
    defaults.update(overrides)
    prop = Property(**defaults)
    session.add(prop)
    session.commit()
    session.refresh(prop)
    return prop


def add_observation(session: Session, prop: Property, src: Source, *, at: datetime) -> Observation:
    row = Observation(
        property_id=prop.id,
        source_id=src.id,
        url=f"https://example.invalid/{prop.public_id}",
        scraped_at=at,
        title=prop.canonical_title,
        price=prop.price,
        listing_visible=True,
    )
    session.add(row)
    session.commit()
    return row


def add_price_change(
    session: Session, prop: Property, *, old: float, new: float, at: datetime
) -> PriceHistory:
    row = PriceHistory(
        property_id=prop.id,
        observed_at=at,
        old_price=old,
        new_price=new,
        delta_abs=new - old,
        delta_pct=(new - old) / old * 100.0,
    )
    session.add(row)
    session.commit()
    return row


def add_status_change(
    session: Session,
    prop: Property,
    *,
    old: str | None,
    new: str,
    at: datetime,
    kind: str = ChangeKind.STATUS_CHANGE,
) -> StatusHistory:
    row = StatusHistory(
        property_id=prop.id, observed_at=at, old_status=old, new_status=new, change_kind=kind
    )
    session.add(row)
    session.commit()
    return row


def add_source_link(
    session: Session, prop: Property, src: Source, *, primary: bool = True
) -> PropertySource:
    row = PropertySource(
        property_id=prop.id,
        source_id=src.id,
        url=f"https://example.invalid/{prop.public_id}",
        role=src.role,
        is_primary_source=primary,
        is_best=True,
        first_seen=prop.first_seen,
        last_seen=prop.last_seen,
        contact_name="Maier",
        contact_kind="private",
    )
    session.add(row)
    session.commit()
    return row


def add_score(session: Session, prop: Property, profile_hash: str, **overrides: Any) -> Score:
    defaults: dict[str, Any] = {
        "property_id": prop.id,
        "profile_hash": profile_hash,
        "fit_score": 82.0,
        "deal_score": 71.0,
        "hidden_score": 64.0,
        "freshness_score": 90.0,
        "confidence_score": 75.0,
        "final_score": 78.0,
        "capital_risk": "moderate",
        "rejected": False,
        "reject_reasons": [],
        "flags": ["grosse flaeche"],
        "breakdown": {"fit": {"land": 20, "features": 12}, "deal": {"preis_pro_qm": 9}},
    }
    defaults.update(overrides)
    row = Score(**defaults)
    session.add(row)
    session.commit()
    return row


def add_cost(session: Session, prop: Property, **overrides: Any) -> CostEstimate:
    defaults: dict[str, Any] = {
        "property_id": prop.id,
        "purchase_price": prop.price,
        "acquisition_costs": 35_000.0,
        "renovation_low": 300_000.0,
        "renovation_mid": 380_000.0,
        "renovation_high": 460_000.0,
        "immediate_capex": 25_000.0,
        "total_low": 790_000.0,
        "total_mid": 879_000.0,
        "total_high": 960_000.0,
        "renovation_tier": "medium",
        "breakdown": {"haus": 210_000, "dach": 70_000},
        "assumptions": ["Dachfläche geschätzt aus Grundfläche"],
    }
    defaults.update(overrides)
    row = CostEstimate(**defaults)
    session.add(row)
    session.commit()
    return row


@pytest.fixture()
def default_profile():
    from hofradar.web.deps import base_profile

    return base_profile(None)


@pytest.fixture()
def seeded(db: Session, source: Source) -> dict[str, Property]:
    """Four properties, each isolating one thing a slider has to do.

    ``near``   - long known, price dropped this week, road route never measured.
    ``far``    - 61 km out, so the distance slider (and only that) must drop it.
    ``pricey`` - well inside the radius but 850k, so the budget slider drops it.
    ``fresh``  - genuinely new this week, and only geocoded to town level.
    """
    near = make_property(db, public_id="HF-0001")
    add_source_link(db, near, source)
    add_observation(db, near, source, at=NOW - timedelta(days=200))
    add_observation(db, near, source, at=NOW - timedelta(days=1))
    add_price_change(db, near, old=470_000.0, new=395_000.0, at=NOW - timedelta(days=1))
    add_cost(db, near)

    far = make_property(
        db,
        public_id="HF-0002",
        canonical_title="Vierseithof im Chiemgau",
        description="Vierseithof mit viel Grund am Ortsrand.",
        town="Traunstein",
        postcode="83278",
        lat=47.87,
        lon=12.64,
        distance_air_km=61.0,
        distance_driving_km=78.5,
        price=260_000.0,
        price_first=260_000.0,
        price_reduction_count=0,
        land_sqm=12_000.0,
        living_sqm=140.0,
        usable_sqm=100.0,
        rooms=5.0,
        year_built=1955,
        first_seen=NOW - timedelta(days=90),
    )
    add_source_link(db, far, source)
    add_observation(db, far, source, at=NOW - timedelta(days=90))

    pricey = make_property(
        db,
        public_id="HF-0004",
        canonical_title="Saniertes Sacherl",
        description="Gepflegtes Anwesen, sofort bezugsfertig.",
        town="Bruckmühl",
        postcode="83052",
        lat=47.88,
        lon=11.92,
        distance_air_km=25.0,
        distance_driving_km=30.0,
        price=850_000.0,
        price_first=850_000.0,
        price_reduction_count=0,
        land_sqm=4_200.0,
        living_sqm=180.0,
        usable_sqm=60.0,
        rooms=7.0,
        year_built=2001,
        condition="good",
        outbuildings=[],
        first_seen=NOW - timedelta(days=45),
    )
    add_source_link(db, pricey, source)
    add_observation(db, pricey, source, at=NOW - timedelta(days=45))

    fresh = make_property(
        db,
        public_id="HF-0003",
        canonical_title="Sacherl mit Obstgarten",
        description="Kleines Sacherl mit Obstgarten und altem Stadel.",
        town="Irschenberg",
        postcode="83737",
        lat=47.83,
        lon=11.92,
        geo_precision="town",
        distance_air_km=12.0,
        price=285_000.0,
        price_first=285_000.0,
        price_reduction_count=0,
        land_sqm=3_100.0,
        living_sqm=110.0,
        usable_sqm=70.0,
        rooms=4.0,
        year_built=1948,
        condition="bad",
        verification_status=VerificationStatus.UNVERIFIED,
        listing_status=ListingStatus.DISCOVERED,
        first_seen=NOW - timedelta(days=2),
        last_seen=NOW,
        last_verified=None,
    )
    add_source_link(db, fresh, source, primary=False)
    add_observation(db, fresh, source, at=NOW - timedelta(days=2))

    return {"near": near, "far": far, "pricey": pricey, "fresh": fresh}
