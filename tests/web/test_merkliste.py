"""The Merkliste: one click on a dossier or a card, one page that lists it."""

from __future__ import annotations

from sqlalchemy import select

from hofradar.db.models import Property


def _prop(db, public_id):
    db.expire_all()
    return db.scalar(select(Property).where(Property.public_id == public_id))


def test_toggle_marks_and_unmarks(client, db, seeded):
    response = client.post("/property/HF-0001/merken", headers={"HX-Request": "true"})
    assert response.status_code == 200
    assert "Gemerkt" in response.text
    assert _prop(db, "HF-0001").shortlisted_at is not None

    response = client.post("/property/HF-0001/merken", headers={"HX-Request": "true"})
    assert "Merken" in response.text and "Gemerkt" not in response.text
    assert _prop(db, "HF-0001").shortlisted_at is None


def test_plain_post_redirects_back_to_the_dossier(client, seeded):
    response = client.post("/property/HF-0001/merken", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/property/HF-0001"


def test_unknown_property_is_404(client, seeded):
    assert client.post("/property/HF-9999/merken").status_code == 404


def test_button_appears_on_dossier_and_cards(client, seeded):
    assert 'hx-post="/property/HF-0001/merken"' in client.get("/property/HF-0001").text
    assert 'hx-post="/property/HF-0001/merken"' in client.get("/api/results").text


def test_merkliste_page_lists_only_marked_rows(client, db, seeded):
    assert "Noch nichts gemerkt" in client.get("/merkliste").text
    client.post("/property/HF-0002/merken")
    html = client.get("/merkliste").text
    assert "HF-0002" in html and "HF-0001" not in html
    assert "1 Objekt" in html or "1 gemerkt" in html


def test_merkliste_ignores_the_saved_search_but_keeps_the_sliders(client, db, seeded):
    # HF-0002 sits at 61 km, so 70 km must still admit it - the point is that
    # the *sliders* (air_km_max/total_budget_max) from the saved cookie do
    # apply, while the *view* filter (q="Nirgendwo", which would exclude
    # every seeded town) is dropped.
    client.post("/property/HF-0002/merken")
    client.get("/?air_km_max=70&total_budget_max=700000&q=Nirgendwo")
    response = client.get("/merkliste", follow_redirects=False)
    assert response.status_code == 200
    assert "HF-0002" in response.text
    assert "0 Treffer" not in response.text


def test_merkliste_shows_a_gate_rejected_row_but_not_an_archived_one(
    client, db, seeded, default_profile
):
    from tests.web.conftest import add_score

    add_score(
        db,
        seeded["pricey"],
        default_profile.profile_hash,
        rejected=True,
        reject_reasons=["PRICE_OVER_HARD_MAX"],
    )
    client.post("/property/HF-0004/merken")
    client.post("/property/HF-0002/merken")
    client.post("/property/HF-0002/triage", data={"user_state": "archived"})

    html = client.get("/merkliste").text
    assert "HF-0004" in html
    assert "HF-0002" not in html
    assert "1 archivierte ausgeblendet" in html


def test_legacy_shortlist_triage_value_lands_on_the_merkliste(client, db, seeded):
    response = client.post("/property/HF-0001/triage", data={"user_state": "shortlist"})
    assert response.status_code == 200
    assert "gemerkt" in response.text
    prop = _prop(db, "HF-0001")
    assert prop.shortlisted_at is not None and prop.user_state is None


def test_shortlist_is_no_longer_a_triage_state(client, seeded):
    html = client.get("/property/HF-0001").text
    assert 'value="shortlist"' not in html


def test_exports_carry_the_mark(client, db, seeded):
    client.post("/property/HF-0001/merken")
    row = next(
        p
        for p in client.get("/api/properties.json").json()["properties"]
        if p["public_id"] == "HF-0001"
    )
    assert row["shortlisted_at"]
    csv = client.get("/api/export.csv").text
    assert "Merkliste" in csv.splitlines()[0]
