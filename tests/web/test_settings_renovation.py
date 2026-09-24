"""The Sanierungsstufe thresholds and cost bands, exposed on /settings.

Covers: the page renders the current values, saving persists them and moves
``profile_hash``, an inverted year pair is rejected without writing, a blank
year field keeps the base value instead of failing validation, and the whole
thing actually changes what a property is estimated to cost.
"""

from __future__ import annotations

from sqlalchemy import select

from hofradar.config import SearchProfile
from hofradar.costmodel import estimate_costs
from hofradar.db.enums import RenovationTier
from hofradar.db.models import SearchProfileRecord
from tests.scoring.factories import make_property


def test_settings_page_renders_renovation_fields(client):
    response = client.get("/settings")
    assert response.status_code == 200
    body = response.text
    assert 'name="renovation.pre_modern_year"' in body
    assert 'name="renovation.modern_year"' in body
    assert 'value="1960"' in body
    assert 'value="1995"' in body
    assert "Sanierung (Kostenmodell)" in body
    assert "Sanierungsstufe" in body  # the section hint


def test_saving_moves_the_renovation_thresholds_and_the_hash(client, db):
    before = SearchProfile()

    response = client.post(
        "/settings",
        data={
            "name": "sanierung-test",
            "renovation.pre_modern_year": "1950",
            "renovation.modern_year": "2000",
            "renovation.light_min": "350",
        },
    )
    assert response.status_code == 200
    assert "gespeichert" in response.text

    record = db.scalar(
        select(SearchProfileRecord).where(SearchProfileRecord.name == "sanierung-test")
    )
    assert record is not None
    assert record.data["renovation"]["pre_modern_year"] == 1950
    assert record.data["renovation"]["modern_year"] == 2000
    assert record.data["renovation"]["light_min"] == 350
    assert record.profile_hash != before.profile_hash


def test_inverted_years_are_rejected_without_a_write(client, db):
    response = client.post(
        "/settings",
        data={
            "name": "kaputte-jahre",
            "renovation.pre_modern_year": "2000",
            "renovation.modern_year": "1950",
        },
    )
    assert response.status_code == 400
    assert "Nicht gespeichert" in response.text

    record = db.scalar(
        select(SearchProfileRecord).where(SearchProfileRecord.name == "kaputte-jahre")
    )
    assert record is None


def test_blank_renovation_field_keeps_the_base_value(client, db):
    client.post(
        "/settings",
        data={"name": "basis-sanierung", "renovation.pre_modern_year": "1955"},
    )

    response = client.post(
        "/settings",
        data={
            "base": "basis-sanierung",
            "name": "basis-sanierung",
            "renovation.pre_modern_year": "",
            "renovation.modern_year": "2010",
        },
    )
    assert response.status_code == 200

    record = db.scalar(
        select(SearchProfileRecord).where(SearchProfileRecord.name == "basis-sanierung")
    )
    assert record.data["renovation"]["pre_modern_year"] == 1955
    assert record.data["renovation"]["modern_year"] == 2010


def test_saved_modern_year_changes_the_estimated_tier(client, db):
    """End-to-end: moving modern_year past a building's year flips its tier."""
    client.post(
        "/settings",
        data={"name": "spaeter-modern", "renovation.modern_year": "2000", "is_default": "1"},
    )
    record = db.scalar(
        select(SearchProfileRecord).where(SearchProfileRecord.name == "spaeter-modern")
    )
    profile = SearchProfile(**record.data)

    prop = make_property(condition=None, year_built=1997, building_features=[])
    cost = estimate_costs(prop, profile)
    assert cost.renovation_tier == RenovationTier.MEDIUM.value

    default_cost = estimate_costs(prop, SearchProfile())
    assert default_cost.renovation_tier == RenovationTier.LIGHT.value
