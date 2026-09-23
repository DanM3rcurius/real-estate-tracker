"""The radar must survive a crawl holding SQLite's write lock.

``pipeline.runner`` runs a whole search as one transaction, so while it runs
every web write fails with "database is locked". The rescore on ``GET /`` is
one such write whenever the requested slider position has no scores yet.
The failure was already wrapped into a notice - and then the page 500ed
anyway, because the failed flush left the session in pending-rollback and
the next SELECT raised. This test holds the lock from a second connection,
exactly as the crawl does, and asks for a profile that needs scoring.

A file-backed database, not the shared in-memory one: a lock needs two
connections, and ``StaticPool`` has exactly one.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from hofradar.db.enums import ListingStatus, PriceType, VerificationStatus
from hofradar.db.models import Property
from hofradar.db.session import Base
from hofradar.web.app import create_app

#: Milliseconds SQLite waits for the lock before giving up. Production waits
#: five seconds (``db.session.SQLITE_BUSY_TIMEOUT_MS``); the test only needs
#: the failure, not the wait.
TEST_BUSY_TIMEOUT_MS = 50

#: How SQLAlchemy's SQLite dialect writes a ``DateTime`` column; a raw
#: ``sqlite3`` write has to match it or the value never reads back.
SQLITE_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S.%f"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "locked.sqlite3"


@pytest.fixture()
def locked_client(db_path: Path) -> Iterator[tuple[TestClient, sqlite3.Connection]]:
    engine = create_engine(f"sqlite:///{db_path}", future=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record):  # pragma: no cover - driver level
        dbapi_conn.execute(f"PRAGMA busy_timeout={TEST_BUSY_TIMEOUT_MS}")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    now = datetime.now(UTC)
    with factory() as session:
        session.add(
            Property(
                public_id="hof-locked",
                canonical_title="Hofstelle mit Stadel",
                town="Aying",
                price=500_000.0,
                price_type=PriceType.ASKING,
                land_sqm=5_000.0,
                living_sqm=150.0,
                year_built=1900,
                distance_air_km=20.0,
                distance_driving_km=25.0,
                lat=47.9,
                lon=11.8,
                geo_precision="exact",
                listing_status=ListingStatus.ACTIVE,
                verification_status=VerificationStatus.VERIFIED,
                first_seen=now,
                last_seen=now,
                evidence={},
                building_features=[],
                outbuildings=["stadel"],
                special_features=[],
                exclusion_flags=[],
                llm_risks=[],
            )
        )
        session.commit()

    # The crawl: a write transaction that stays open for the whole request.
    crawl = sqlite3.connect(db_path, isolation_level=None)
    crawl.execute("BEGIN IMMEDIATE")
    crawl.execute("UPDATE properties SET town = town WHERE public_id = 'hof-locked'")

    app = create_app(session_factory=factory)
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client, crawl
    crawl.rollback()
    crawl.close()
    engine.dispose()


def test_the_radar_renders_with_a_notice_while_a_crawl_holds_the_lock(
    locked_client: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, _crawl = locked_client
    # A slider position nobody has scored yet, so the request has to write.
    response = client.get("/?air_km_max=98.0&total_budget_max=750000.0&sort=price")

    assert response.status_code == 200
    assert "gesperrt" in response.text
    assert "Crawl" in response.text
    assert "hofradar migrate" not in response.text


def test_the_merkliste_still_shows_a_mark_while_the_lock_is_held(
    locked_client: tuple[TestClient, sqlite3.Connection],
    db_path: Path,
) -> None:
    """A rescore that cannot write must not cost the reader their bookmarks.

    ``ranked_properties`` joins ``Score`` on the live profile hash, so with no
    score row written the marked property is simply not in the ranking. It
    used to vanish from the Merkliste under the sentence "Alle gemerkten
    Objekte sind archiviert", which was not true of it.
    """
    client, crawl = locked_client
    # The reader marked it *before* the crawl started, which is the real
    # sequence: while the crawl holds the lock no web write succeeds at all.
    crawl.rollback()
    marker = sqlite3.connect(db_path)
    marker.execute(
        "UPDATE properties SET shortlisted_at = ? WHERE public_id = 'hof-locked'",
        # The format SQLite's DATETIME round-trips, not an ISO string.
        (datetime.now(UTC).strftime(SQLITE_DATETIME_FORMAT),),
    )
    marker.commit()
    marker.close()
    crawl.execute("BEGIN IMMEDIATE")
    crawl.execute("UPDATE properties SET town = town WHERE public_id = 'hof-locked'")

    response = client.get("/merkliste")

    assert response.status_code == 200
    assert "hof-locked" in response.text
    assert "archiviert" not in response.text


def test_the_same_request_scores_normally_once_the_lock_is_released(
    locked_client: tuple[TestClient, sqlite3.Connection],
) -> None:
    client, crawl = locked_client
    crawl.rollback()
    response = client.get("/?air_km_max=98.0&total_budget_max=750000.0&sort=price")

    assert response.status_code == 200
    assert "gesperrt" not in response.text


def test_a_rename_during_a_crawl_says_it_was_not_saved(
    locked_client: tuple[TestClient, sqlite3.Connection],
) -> None:
    """htmx swaps nothing on a 500, so an unhandled lock was a click that
    silently did nothing. The reader gets the fold back, draft and all."""
    client, _crawl = locked_client
    response = client.post(
        "/property/hof-locked/title", data={"title": "Moarhof"}, headers={"HX-Request": "true"}
    )

    assert response.status_code == 200
    assert "Nicht gespeichert" in response.text
    assert "gesperrt" in response.text
    assert 'value="Moarhof"' in response.text
    assert "<h1>Hofstelle mit Stadel</h1>" in response.text

    plain = client.post("/property/hof-locked/title", data={"title": "Moarhof"})
    assert plain.status_code == 503
    assert "gesperrt" in plain.text
