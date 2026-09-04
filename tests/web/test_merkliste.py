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
    # every seeded town) is dropped. The default profile's own air_km_max is
    # 80 km, so a bare "HF-0002 is present" assertion would pass even if the
    # saved cookie were never read at all - pin the exact rendered slider
    # value (de_km(0) of 70) so the cookie is actually proven to be in play.
    client.post("/property/HF-0002/merken")
    client.get("/?air_km_max=70&total_budget_max=700000&q=Nirgendwo")
    response = client.get("/merkliste", follow_redirects=False)
    assert response.status_code == 200
    assert "HF-0002" in response.text
    assert "0 Treffer" not in response.text
    assert "70 km" in response.text


def test_merkliste_shows_a_gate_rejected_row_but_not_an_archived_one(client, db, seeded):
    """The score gate (``Score.rejected``) is bypassed on the Merkliste; an
    archived property is still hidden (and still counted), same as the radar.

    ``build_results`` recomputes the score for any property missing one for
    the active profile hash, so a hand-written ``Score(rejected=True)`` row
    gets overwritten before this test ever reads it back - the gate has to be
    tripped for real. ``REJECT_LISTING_GONE`` fires on ``listing_status`` and
    is not one of ``passes_profile``'s own checks (distance, price, total
    cost), so it isolates the score gate from the web layer's unconditional
    slider filter: HF-0004 (well inside the default sliders) is rejected by
    the *scorer* alone.
    """
    from hofradar.db.enums import ListingStatus

    seeded["pricey"].listing_status = ListingStatus.SOLD
    db.commit()

    client.post("/property/HF-0004/merken")
    client.post("/property/HF-0002/merken")
    client.post("/property/HF-0002/triage", data={"user_state": "archived"})

    radar_html = client.get("/").text
    assert "HF-0004" not in radar_html  # score-gate rejected, hidden from the radar

    merkliste_html = client.get("/merkliste").text
    assert "HF-0004" in merkliste_html
    assert "HF-0002" not in merkliste_html
    assert "1 archivierte ausgeblendet" in merkliste_html


def test_merkliste_keeps_a_mark_outside_the_sliders(client, db, seeded):
    """Decision 21: the Merkliste is the human's list, not subject to the
    machine's profile gate. HF-0002 sits at 61 km, outside a 30 km radius, so
    it must still show up marked, and the page must not claim nothing was
    marked."""
    client.post("/property/HF-0002/merken")
    client.get("/?air_km_max=30&total_budget_max=700000")
    html = client.get("/merkliste").text
    assert "HF-0002" in html
    assert "Noch nichts gemerkt" not in html


def test_merkliste_archived_count_is_scoped_to_marked_rows(client, db, seeded):
    """Archiving an unmarked property must not appear in the Merkliste's own
    counts - its 'archivierte ausgeblendet' figure is over the marked set,
    not the whole database. An empty Merkliste keeps saying nothing was
    marked, never the radar's 'nothing passes the sliders' copy."""
    client.post("/property/HF-0003/triage", data={"user_state": "archived"})
    html = client.get("/merkliste").text
    assert "archivierte ausgeblendet" not in html
    assert "Noch nichts gemerkt" in html
    assert "Kein Objekt passt zu diesen Reglern" not in html


def test_merkliste_all_marked_archived_is_an_honest_message(client, db, seeded):
    """Mark and archive the same property: the Merkliste is not empty of
    marks, it is empty of *visible* marks - a different sentence from 'you
    marked nothing'."""
    client.post("/property/HF-0002/merken")
    client.post("/property/HF-0002/triage", data={"user_state": "archived"})
    html = client.get("/merkliste").text
    assert "1 archivierte ausgeblendet" in html
    assert "Alle gemerkten Objekte sind archiviert" in html
    assert "Noch nichts gemerkt" not in html


def test_merkliste_view_only_params_do_not_suppress_the_saved_sliders(client, db, seeded):
    """A stray query param (``limit``, a tracking parameter, ...) must not
    reset the sliders to the default profile - only air_km_max/total_budget_max
    may override the saved cookie."""
    client.get("/?air_km_max=70&total_budget_max=700000")
    response = client.get("/merkliste?limit=20")
    assert "70 km" in response.text


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
