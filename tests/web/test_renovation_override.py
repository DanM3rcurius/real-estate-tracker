"""The reader sets the Sanierungsstufe on the dossier; every euro follows at once.

The tier is the single biggest input to the cost model, and the rules that
infer it are deliberately pessimistic stand-ins for a look nobody had taken.
Once the reader has taken it, their tier wins - and the page must show the new
figures on the very next render, or the setting reads as ignored. What the
rules would have said stays on the page beside it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from hofradar.costmodel import automatic_renovation_tier, estimate_costs
from hofradar.db.models import CostEstimate, Property, Score
from hofradar.web.deps import base_profile
from hofradar.web.filters import de_tier
from tests.web.test_locked_database import db_path, locked_client  # noqa: F401 - fixtures

HX = {"HX-Request": "true"}
SEEDED_COST_MID = 380_000.0  # conftest.add_cost's renovation_mid for HF-0001


def _prop(db, public_id: str) -> Property:
    db.expire_all()
    return db.scalar(select(Property).where(Property.public_id == public_id))


def _cost(db, prop: Property) -> CostEstimate | None:
    db.expire_all()
    return db.scalar(select(CostEstimate).where(CostEstimate.property_id == prop.id))


def test_dossier_offers_the_tier_form_without_javascript(client, seeded):
    html = client.get("/property/HF-0001").text
    assert 'id="kostenmodell"' in html
    assert 'action="/property/HF-0001/sanierung"' in html
    assert "Automatisch (aus Inserat/Baujahr)" in html
    for word in ("Leicht", "Mittel", "Schwer", "Kernsanierung"):
        assert f">{word}</option>" in html
    assert '<option value="auto" selected>' in html
    assert "Übernehmen" in html
    assert "automatisch wäre" not in html


def test_setting_a_tier_recomputes_cost_and_score_at_once(client, db, seeded):
    response = client.post(
        "/property/HF-0001/sanierung", data={"tier": "complete"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/property/HF-0001?")
    assert response.headers["location"].endswith("#kostenmodell")

    prop = _prop(db, "HF-0001")
    assert prop.user_renovation_tier == "complete"

    profile = base_profile(db)
    cost = _cost(db, prop)
    assert cost.renovation_tier == "complete"
    assert cost.renovation_mid == estimate_costs(prop, profile).renovation_mid
    assert cost.renovation_mid != SEEDED_COST_MID

    score = db.scalar(
        select(Score).where(Score.property_id == prop.id, Score.profile_hash == profile.profile_hash)
    )
    assert score is not None
    assert score.breakdown["cost"]["renovation_tier"] == "complete"

    html = client.get(response.headers["location"]).text
    assert "<b>Kernsanierung</b>" in html
    assert "manuell gesetzt" in html
    auto_word = de_tier(automatic_renovation_tier(prop, profile.renovation).value)
    assert f"automatisch wäre: {auto_word}" in html
    assert '<option value="complete" selected>' in html
    assert "Kostenmodell neu berechnet" in html


def test_auto_clears_back_to_the_inferred_tier(client, db, seeded):
    client.post("/property/HF-0001/sanierung", data={"tier": "complete"})
    client.post("/property/HF-0001/sanierung", data={"tier": "auto"})

    prop = _prop(db, "HF-0001")
    assert prop.user_renovation_tier is None
    inferred = automatic_renovation_tier(prop, base_profile(db).renovation).value
    assert _cost(db, prop).renovation_tier == inferred

    html = client.get("/property/HF-0001").text
    assert "· manuell gesetzt" not in html
    assert "automatisch wäre" not in html
    assert '<option value="auto" selected>' in html


def test_an_empty_value_also_clears(client, db, seeded):
    client.post("/property/HF-0001/sanierung", data={"tier": "heavy"})
    client.post("/property/HF-0001/sanierung", data={"tier": ""})
    assert _prop(db, "HF-0001").user_renovation_tier is None


def test_an_invalid_tier_is_refused_and_nothing_is_written(client, db, seeded):
    client.post("/property/HF-0001/sanierung", data={"tier": "heavy"})

    for bad in ("unknown", "leicht", "ruine"):
        response = client.post("/property/HF-0001/sanierung", data={"tier": bad})
        assert response.status_code == 400
        assert "keine Sanierungsstufe" in response.text
        assert _prop(db, "HF-0001").user_renovation_tier == "heavy"
        assert _cost(db, _prop(db, "HF-0001")).renovation_tier == "heavy"


def test_unknown_property_is_404(client, seeded):
    assert client.post("/property/HF-9999/sanierung", data={"tier": "light"}).status_code == 404


def test_a_merged_away_row_sets_the_survivors_tier(client, db, seeded):
    near, far = seeded["near"], seeded["far"]
    dropped = _prop(db, far.public_id)
    dropped.merged_into_id = near.id
    db.commit()

    response = client.post(
        f"/property/{far.public_id}/sanierung", data={"tier": "light"}, follow_redirects=False
    )
    assert response.headers["location"].startswith(f"/property/{near.public_id}?")
    assert _prop(db, near.public_id).user_renovation_tier == "light"
    assert _prop(db, far.public_id).user_renovation_tier is None


def test_htmx_is_sent_to_the_reloaded_dossier(client, db, seeded):
    response = client.post("/property/HF-0001/sanierung", data={"tier": "light"}, headers=HX)
    assert response.status_code == 204
    assert response.headers["HX-Redirect"].startswith("/property/HF-0001?")


def test_the_json_carries_the_readers_tier(client, seeded):
    assert client.get("/api/property/HF-0001.json").json()["user_renovation_tier"] is None
    client.post("/property/HF-0001/sanierung", data={"tier": "medium"})
    assert client.get("/api/property/HF-0001.json").json()["user_renovation_tier"] == "medium"


def test_the_form_shows_even_without_a_cost_model(client, db, seeded):
    """HF-0002 has no CostEstimate row: the tier is still visible and settable."""
    far = seeded["far"]
    html = client.get(f"/property/{far.public_id}").text
    assert "noch kein Kostenmodell gerechnet" in html
    assert f'action="/property/{far.public_id}/sanierung"' in html

    client.post(f"/property/{far.public_id}/sanierung", data={"tier": "light"})
    assert _cost(db, _prop(db, far.public_id)).renovation_tier == "light"


def test_a_locked_recompute_keeps_the_tier_and_says_so(client, db, seeded, monkeypatch):
    """The tier is the reader's decision and is committed first; the derived
    figures may wait for the radar's next rescore, but the page must say so."""
    import hofradar.scoring as scoring

    def _locked(*_args, **_kwargs):
        raise OperationalError("UPDATE cost_estimates", {}, Exception("database is locked"))

    monkeypatch.setattr(scoring, "rescore_property", _locked)

    response = client.post(
        "/property/HF-0001/sanierung", data={"tier": "complete"}, follow_redirects=False
    )
    assert response.status_code == 303
    prop = _prop(db, "HF-0001")
    assert prop.user_renovation_tier == "complete"
    assert _cost(db, prop).renovation_mid == SEEDED_COST_MID

    html = client.get(response.headers["location"]).text
    assert "nicht neu berechnet werden" in html
    assert "Crawl" in html


def test_a_tier_during_a_crawl_is_not_saved_and_says_so(
    locked_client: tuple[TestClient, sqlite3.Connection],  # noqa: F811 - fixture
    db_path: Path,  # noqa: F811 - fixture
) -> None:
    client, _crawl = locked_client
    response = client.post("/property/hof-locked/sanierung", data={"tier": "light"})

    assert response.status_code == 503
    assert "gesperrt" in response.text
    assert "Crawl" in response.text

    reader = sqlite3.connect(db_path)
    try:
        stored = reader.execute(
            "SELECT user_renovation_tier FROM properties WHERE public_id = 'hof-locked'"
        ).fetchone()
    finally:
        reader.close()
    assert stored == (None,)
