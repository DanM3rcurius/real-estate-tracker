"""The legacy ``user_state='shortlist'`` value must become ``shortlisted_at``.

Mirrors ``tests/db/test_migrations.py``: a database built from the migrations
alone, upgraded to the revision just before this one, seeded by hand with the
old triage value, then upgraded to head and checked.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, text

from hofradar.db.migrate import alembic_config

#: The head this feature's migration is layered on top of.
PREVIOUS_HEAD = "313e03064089"


def _engine(tmp_path: Path):
    return create_engine(f"sqlite:///{tmp_path / 'db.sqlite3'}")


def test_legacy_shortlist_becomes_shortlisted_at(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), PREVIOUS_HEAD)
        connection.execute(
            text(
                """
                INSERT INTO properties (
                    public_id, canonical_title, country, geo_precision, price_type,
                    price_reduction_count, building_features, outbuildings,
                    special_features, exclusion_flags, evidence, listing_status,
                    verification_status, first_seen, last_seen, is_foreclosure,
                    is_monument, is_private_seller, is_off_market_signal, llm_risks,
                    user_state, created_at, updated_at
                ) VALUES (
                    'HF-MIG-0001', 'Testhof', 'DE', 'none', 'unknown',
                    0, '[]', '[]',
                    '[]', '[]', '{}', 'discovered',
                    'unverified', '2026-01-01 00:00:00', '2026-01-01 00:00:00', 0,
                    0, 0, 0, '[]',
                    'shortlist', '2026-01-01 00:00:00', '2026-02-01 12:00:00'
                )
                """
            )
        )

    with engine.begin() as connection:
        command.upgrade(alembic_config(connection), "head")

    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT shortlisted_at, user_state, updated_at FROM properties "
                 "WHERE public_id = 'HF-MIG-0001'")
        ).one()

    assert row.shortlisted_at is not None
    assert row.user_state is None
    assert row.shortlisted_at == row.updated_at
