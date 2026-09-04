"""The Verlauf tells the story once: no duplicate first-seen row, German status
names, and one line per run of observations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hofradar.db.enums import ChangeKind, ListingStatus
from hofradar.db.models import Observation, StatusHistory
from hofradar.web import history
from tests.web.conftest import make_property

T0 = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)


def _observe(db, prop, source, at, visible=True):
    db.add(Observation(property_id=prop.id, source_id=source.id, scraped_at=at,
                       listing_visible=visible, url=f"https://x.test/{at.timestamp()}"))


def test_first_seen_status_row_is_not_repeated(db, seeded, source):
    prop = seeded["near"]
    db.add(StatusHistory(property_id=prop.id, observed_at=T0, old_status=None,
                         new_status=ListingStatus.DISCOVERED, change_kind=ChangeKind.FIRST_SEEN))
    db.commit()
    db.refresh(prop)
    titles = [e["title"] for e in history.timeline(prop)]
    assert titles.count("Erstmals erfasst") == 1
    assert "Statuswechsel" not in titles or all(
        "unbekannt" not in e["text"] for e in history.timeline(prop) if e["title"] == "Statuswechsel"
    )


def test_status_change_uses_german_words(db, seeded):
    prop = seeded["near"]
    db.add(StatusHistory(property_id=prop.id, observed_at=T0, old_status=ListingStatus.DISCOVERED,
                         new_status=ListingStatus.ACTIVE, change_kind=ChangeKind.STATUS_CHANGE))
    db.add(StatusHistory(property_id=prop.id, observed_at=T0 + timedelta(days=1),
                         old_status=ListingStatus.ACTIVE, new_status=ListingStatus.ACTIVE,
                         change_kind=ChangeKind.STATUS_CHANGE))
    db.commit()
    db.refresh(prop)
    changes = [e for e in history.timeline(prop) if e["title"] == "Statuswechsel"]
    assert len(changes) == 1
    assert "„Entdeckt“ → „Aktiv“" in changes[0]["text"]
    assert "discovered" not in changes[0]["text"]


def test_consecutive_observations_fold_into_one_line(db, source):
    prop = make_property(db, public_id="HF-0100")
    for day in range(5):
        _observe(db, prop, source, T0 + timedelta(days=day))
    db.commit()
    db.refresh(prop)
    folded = [e for e in history.timeline(prop) if e["kind"] == "observations"]
    assert len(folded) == 1
    assert "5 Abrufe" in folded[0]["text"]
    assert folded[0]["at"] == T0 + timedelta(days=4)


def test_a_price_change_between_observations_starts_a_new_group(db, source):
    from hofradar.db.models import PriceHistory

    prop = make_property(db, public_id="HF-0100")
    _observe(db, prop, source, T0)
    _observe(db, prop, source, T0 + timedelta(days=1))
    db.add(PriceHistory(property_id=prop.id, observed_at=T0 + timedelta(days=2),
                        old_price=500_000, new_price=450_000, delta_pct=-10.0))
    _observe(db, prop, source, T0 + timedelta(days=3))
    db.commit()
    db.refresh(prop)
    kinds = [e["kind"] for e in history.timeline(prop) if e["kind"] in ("observations", "observation", "price_change")]
    assert kinds == ["observations", "price_change", "observation"]


def test_last_unreachable_observation_says_so(db, source):
    prop = make_property(db, public_id="HF-0100")
    _observe(db, prop, source, T0)
    _observe(db, prop, source, T0 + timedelta(days=1), visible=False)
    db.commit()
    db.refresh(prop)
    folded = [e for e in history.timeline(prop) if e["kind"] == "observations"]
    assert "nicht mehr erreichbar" in folded[0]["text"]
