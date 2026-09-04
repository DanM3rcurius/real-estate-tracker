"""Deleting a property, and the two ways that goes wrong quietly.

The engine here is a real file database built through ``get_engine`` on
purpose. Every other fixture in the suite uses a bare ``create_engine``, where
SQLite defaults to ``PRAGMA foreign_keys=0`` and a delete leaves orphans behind
without complaining - ``verification_events`` has no ORM relationship, so it is
the DB-level cascade or nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session

from hofradar.db.enums import DocumentKind, SourceRole
from hofradar.db.models import (
    CostEstimate,
    Document,
    Image,
    Observation,
    PriceHistory,
    Property,
    PropertySource,
    Score,
    Source,
    StatusHistory,
    VerificationEvent,
)
from hofradar.db.session import get_engine, init_db
from hofradar.lifecycle import ResurrectsMergedDuplicates, delete_property, dependent_rows

NOW = datetime(2026, 9, 4, 9, 0, tzinfo=UTC)

#: Every table that hangs off a property, and the column that points back.
CHILD_TABLES = (
    "observations",
    "property_sources",
    "price_history",
    "status_history",
    "images",
    "documents",
    "scores",
    "cost_estimates",
    "verification_events",
)


@pytest.fixture()
def fk_engine(tmp_path) -> Engine:
    engine = get_engine(f"sqlite:///{tmp_path / 'hofradar.sqlite3'}")
    init_db(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def fk_session(fk_engine: Engine) -> Session:
    with Session(fk_engine, future=True) as session:
        yield session


def test_the_fixture_really_has_foreign_keys_on(fk_session: Session):
    assert fk_session.scalar(text("PRAGMA foreign_keys")) == 1


def _populate(session: Session, *, public_id: str = "HF-DEL") -> Property:
    """One property with a row in every table that hangs off it."""
    source = Source(key="testportal", name="Testportal", role=SourceRole.PRIMARY, enabled=True)
    session.add(source)
    session.flush()

    prop = Property(public_id=public_id, canonical_title="Hofstelle mit Stadel", town="Miesbach")
    session.add(prop)
    session.flush()

    session.add_all(
        [
            Observation(property_id=prop.id, source_id=source.id, url="https://x.invalid/1"),
            PropertySource(
                property_id=prop.id,
                source_id=source.id,
                url="https://x.invalid/1",
                role=source.role,
            ),
            PriceHistory(property_id=prop.id, observed_at=NOW, new_price=395_000.0),
            StatusHistory(property_id=prop.id, observed_at=NOW, new_status="active"),
            Image(property_id=prop.id, url="https://x.invalid/1.jpg"),
            Document(
                property_id=prop.id,
                kind=DocumentKind.EXPOSE,
                document_url="https://x.invalid/1.pdf",
            ),
            Score(property_id=prop.id, profile_hash="abc123"),
            CostEstimate(property_id=prop.id, purchase_price=395_000.0),
            VerificationEvent(
                property_id=prop.id, url="https://x.invalid/1", outcome="verified"
            ),
        ]
    )
    session.commit()
    return prop


def _orphans(session: Session, property_id: int) -> dict[str, int]:
    return {
        table: session.scalar(
            text(f"SELECT count(*) FROM {table} WHERE property_id = :pid"),  # noqa: S608
            {"pid": property_id},
        )
        for table in CHILD_TABLES
    }


def test_delete_leaves_no_orphan_in_any_child_table(fk_session: Session):
    prop = _populate(fk_session)
    property_id = prop.id
    assert all(count == 1 for count in _orphans(fk_session, property_id).values())

    delete_property(fk_session, prop, backup=False)

    assert fk_session.scalar(select(Property).where(Property.id == property_id)) is None
    assert _orphans(fk_session, property_id) == dict.fromkeys(CHILD_TABLES, 0)


def test_the_report_names_what_went_with_it(fk_session: Session):
    prop = _populate(fk_session)
    report = delete_property(fk_session, prop, backup=False)
    assert report.public_id == "HF-DEL"
    assert report.children["observations"] == 1
    assert report.children["verification_events"] == 1
    assert report.backup_path is None
    assert "HF-DEL" in report.summary()


def test_dependent_rows_counts_without_deleting(fk_session: Session):
    prop = _populate(fk_session)
    counts = dependent_rows(fk_session, prop)
    assert counts["observations"] == 1
    assert fk_session.get(Property, prop.id) is not None


def test_deleting_a_merge_survivor_is_refused(fk_session: Session):
    survivor = _populate(fk_session, public_id="HF-KEEP")
    duplicate = Property(
        public_id="HF-DUP", canonical_title="Dieselbe Hofstelle", merged_into_id=survivor.id
    )
    fk_session.add(duplicate)
    fk_session.commit()

    with pytest.raises(ResurrectsMergedDuplicates) as excinfo:
        delete_property(fk_session, survivor, backup=False)

    assert "HF-DUP" in str(excinfo.value)
    assert fk_session.get(Property, survivor.id) is not None


def test_the_merged_duplicate_itself_can_be_deleted(fk_session: Session):
    survivor = _populate(fk_session, public_id="HF-KEEP")
    duplicate = Property(
        public_id="HF-DUP", canonical_title="Dieselbe Hofstelle", merged_into_id=survivor.id
    )
    fk_session.add(duplicate)
    fk_session.commit()

    delete_property(fk_session, duplicate, backup=False)
    assert fk_session.get(Property, survivor.id) is not None


def test_a_backup_is_written_before_the_delete(fk_session: Session, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    prop = _populate(fk_session)
    report = delete_property(fk_session, prop)

    assert report.backup_path is not None
    assert report.backup_path.exists()
    # The snapshot predates the delete: the row is still in it.
    with Session(create_engine(f"sqlite:///{report.backup_path}"), future=True) as backup:
        assert backup.scalar(select(Property).where(Property.public_id == "HF-DEL")) is not None
