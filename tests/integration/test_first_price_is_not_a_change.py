"""A property becoming known is not a price change.

Ingest journals the opening price as a ``PriceHistory`` row with
``old_price = None``. Read naively that looks like a movement, and a live smoke
run duly rendered a brand-new property with a NEU chip and a PREISAENDERUNG
chip side by side, plus a timeline entry reading "Preis von - auf 480.000 EUR
gestiegen". Both are pinned here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hofradar.db.models import PriceHistory, Property
from hofradar.web import history


def _property_with_opening_price(price: float = 480_000.0) -> Property:
    now = datetime.now(UTC)
    prop = Property(
        public_id="hof-first-price",
        canonical_title="Resthof",
        price=price,
        price_first=price,
        first_seen=now,
        last_seen=now,
    )
    prop.price_history = [PriceHistory(observed_at=now, old_price=None, new_price=price)]
    return prop


def test_the_opening_price_is_not_counted_as_a_change() -> None:
    prop = _property_with_opening_price()
    since = datetime.now(UTC) - timedelta(days=7)

    assert history.price_events_since(prop, since) == []


def test_a_real_reduction_is_counted() -> None:
    prop = _property_with_opening_price(790_000.0)
    now = datetime.now(UTC)
    prop.price_history.append(
        PriceHistory(
            observed_at=now,
            old_price=790_000.0,
            new_price=749_000.0,
            delta_abs=-41_000.0,
            delta_pct=-5.19,
        )
    )
    since = now - timedelta(days=7)

    events = history.price_events_since(prop, since)
    assert len(events) == 1
    assert events[0].old_price == 790_000.0


def test_the_timeline_words_the_opening_price_correctly() -> None:
    prop = _property_with_opening_price()

    entries = history.timeline(prop)
    opening = [e for e in entries if e["kind"] == "price_first"]

    assert len(opening) == 1
    assert "Erster bekannter Preis" in opening[0]["text"]
    assert not any(e["kind"] == "price_change" for e in entries)
