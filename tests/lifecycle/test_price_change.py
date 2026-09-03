"""Blueprint Test 5 - 790,000 EUR becomes 749,000 EUR."""

from __future__ import annotations

import pytest

from hofradar.db.enums import ChangeKind, SourceRole
from hofradar.db.models import PriceHistory
from hofradar.lifecycle import ingest


def test_price_reduction_is_reported_with_delta_and_history(
    db_session, make_source, make_listing
):
    source = make_source("bauernhoefe", role=SourceRole.PRIMARY)
    url = "https://bauernhoefe.example/objekt/4711"
    facts = dict(source_key=source.key, url=url, land_sqm=8500, living_sqm=220, year_built=1890)

    prop, first = ingest(db_session, make_listing(price=790_000, **facts), source=source, run_id=1)
    assert first.kind == ChangeKind.FIRST_SEEN
    assert prop.price_first == 790_000

    same, change = ingest(db_session, make_listing(price=749_000, **facts), source=source, run_id=2)

    assert same.id == prop.id
    assert change.kind == ChangeKind.PRICE_CHANGE
    assert change.old_price == 790_000
    assert change.new_price == 749_000
    assert change.delta_abs == pytest.approx(-41_000)
    assert change.delta_pct == pytest.approx(-5.19, abs=0.01)

    history = db_session.query(PriceHistory).filter_by(property_id=prop.id).all()
    assert len(history) == 2  # the baseline point, then the reduction
    reduction = history[-1]
    assert reduction.old_price == 790_000
    assert reduction.new_price == 749_000
    assert reduction.delta_pct == pytest.approx(-5.19, abs=0.01)

    assert same.price == 749_000
    assert same.price_first == 790_000
    assert same.price_reduction_count == 1


def test_an_unchanged_price_writes_no_history(db_session, make_source, make_listing):
    source = make_source("bauernhoefe")
    listing = make_listing(source_key=source.key, price=790_000, land_sqm=8500, living_sqm=220)

    prop, _ = ingest(db_session, listing, source=source, run_id=1)
    _, change = ingest(db_session, listing, source=source, run_id=2)

    assert change.kind == ChangeKind.UNCHANGED
    assert db_session.query(PriceHistory).filter_by(property_id=prop.id).count() == 1


def test_a_price_increase_does_not_count_as_a_reduction(db_session, make_source, make_listing):
    source = make_source("bauernhoefe")
    facts = dict(source_key=source.key, url="https://x.example/1", land_sqm=8500, living_sqm=220)

    prop, _ = ingest(db_session, make_listing(price=700_000, **facts), source=source)
    _, change = ingest(db_session, make_listing(price=730_000, **facts), source=source)

    assert change.kind == ChangeKind.PRICE_CHANGE
    assert change.delta_abs == pytest.approx(30_000)
    assert prop.price_reduction_count == 0
