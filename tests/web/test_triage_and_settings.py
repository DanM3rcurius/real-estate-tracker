"""Human judgement and saved profiles - the two things that must survive re-runs."""

from __future__ import annotations

from sqlalchemy import select

from hofradar.db.models import Property, SearchProfileRecord


def test_triage_post_persists_user_state(client, db, seeded):
    response = client.post(
        "/property/HF-0001/triage",
        data={"user_state": "shortlist", "user_note": "Anrufen, Stadel ansehen."},
    )
    assert response.status_code == 200
    assert "Gespeichert" in response.text

    db.expire_all()
    prop = db.scalar(select(Property).where(Property.public_id == "HF-0001"))
    assert prop.user_state == "shortlist"
    assert prop.user_note == "Anrufen, Stadel ansehen."


def test_triage_survives_a_profile_change(client, db, seeded):
    client.post("/property/HF-0001/triage", data={"user_state": "watch", "user_note": "später"})
    client.get("/api/results?air_km_max=30&total_budget_max=500000")
    client.get("/api/results?air_km_max=180&total_budget_max=2500000")

    db.expire_all()
    prop = db.scalar(select(Property).where(Property.public_id == "HF-0001"))
    assert prop.user_state == "watch"


def test_triage_rejects_unknown_state_without_500(client, db, seeded):
    response = client.post("/property/HF-0001/triage", data={"user_state": "nonsense"})
    assert response.status_code == 200
    db.expire_all()
    prop = db.scalar(select(Property).where(Property.public_id == "HF-0001"))
    assert prop.user_state is None


def test_triage_none_clears_the_flag(client, db, seeded):
    client.post("/property/HF-0001/triage", data={"user_state": "rejected"})
    client.post("/property/HF-0001/triage", data={"user_state": "none"})
    db.expire_all()
    prop = db.scalar(select(Property).where(Property.public_id == "HF-0001"))
    assert prop.user_state is None


def test_settings_saves_a_profile(client, db):
    response = client.post(
        "/settings",
        data={
            "name": "eng",
            "radius.air_km_max": "35",
            "budget.total_budget_max": "700000",
            "gates.shortlist_size": "5",
            "is_default": "1",
        },
    )
    assert response.status_code == 200
    assert "gespeichert" in response.text

    record = db.scalar(select(SearchProfileRecord).where(SearchProfileRecord.name == "eng"))
    assert record is not None
    assert record.is_default is True
    assert record.data["radius"]["air_km_max"] == 35
    assert record.data["budget"]["total_budget_max"] == 700000
    assert record.profile_hash


def test_saved_default_profile_becomes_the_slider_starting_point(client, db):
    client.post(
        "/settings",
        data={"name": "eng", "radius.air_km_max": "35", "budget.total_budget_max": "700000",
              "is_default": "1"},
    )
    html = client.get("/").text
    assert 'id="air_km_max"' in html
    assert 'value="35.0"' in html or 'value="35"' in html


def test_settings_duplicate_and_delete(client, db):
    client.post("/settings", data={"name": "basis", "radius.air_km_max": "50"})
    record = db.scalar(select(SearchProfileRecord).where(SearchProfileRecord.name == "basis"))

    client.post(f"/settings/{record.id}/duplicate", follow_redirects=False)
    copy = db.scalar(
        select(SearchProfileRecord).where(SearchProfileRecord.name == "basis (Kopie)")
    )
    assert copy is not None

    copy_id = copy.id
    client.post(f"/settings/{copy_id}/delete", follow_redirects=False)
    db.expunge_all()
    assert (
        db.scalar(select(SearchProfileRecord).where(SearchProfileRecord.id == copy_id)) is None
    )


def test_settings_rejects_an_invalid_profile_without_500(client):
    response = client.post(
        "/settings",
        data={"name": "kaputt", "budget.purchase_share_of_total": "5"},
    )
    assert response.status_code == 400
    assert "Nicht gespeichert" in response.text


def test_add_page_reports_a_missing_module_instead_of_crashing(client):
    """``normalize``/``lifecycle`` may not exist yet - the paste must not be lost."""
    response = client.post("/add", data={"text": "Schöner Vierseithof, 8.000 m² Grund, 590.000 €"})
    assert response.status_code == 200
    body = response.text
    assert "Übernommen als" in body or "nicht verfügbar" in body or "fehlgeschlagen" in body


def test_add_requires_input(client):
    response = client.post("/add", data={"url": "", "text": ""})
    assert response.status_code == 400
    assert "Inserats-URL oder einen Exposé-Text" in response.text


def test_run_button_answers_without_blocking(client, monkeypatch):
    """The button must answer immediately, and never 500 if the pipeline is absent.

    The background worker itself is stubbed out: this test is about the web
    layer's response, and a real crawl has no business running offline.
    """
    from hofradar.web.routes import runs

    async def _noop(profile):  # noqa: ANN001, ARG001
        return None

    monkeypatch.setattr(runs, "_execute", _noop)

    response = client.post("/api/run")
    assert response.status_code == 200
    assert "gestartet" in response.text or "nicht verfügbar" in response.text
