"""The reader sets the Sanierungsstufe; the numbers follow at once.

The inferred tier is a guess from an advert. A reader who has walked through
the building overrides it from the dossier, the cost estimate and the score
are recomputed in the same transaction, and the listing's own tier stays on
the page beside it so the reader can see what they overrode.
"""

from __future__ import annotations

import re

from sqlalchemy import select

from hofradar.db.models import CostEstimate, Property, Score
from tests.web.conftest import make_property

ROUTE = "/property/HF-0001/sanierungsstufe"


def _prop(db, public_id: str = "HF-0001") -> Property:
    db.expire_all()
    return db.scalar(select(Property).where(Property.public_id == public_id))


def _cost(db, prop: Property) -> CostEstimate:
    db.expire_all()
    return db.scalar(select(CostEstimate).where(CostEstimate.property_id == prop.id))


def test_dossier_offers_the_tier_form(client, seeded):
    html = client.get("/property/HF-0001").text
    assert 'action="/property/HF-0001/sanierungsstufe"' in html
    assert "Sanierungsstufe ändern" in html
    # Every tier the reader may pick, each with the band it will be priced at.
    for word in ("leicht", "mittel", "schwer", "Kernsanierung"):
        assert word in html
    assert "€/m²" in html
    # Nothing set: "Automatisch" is the checked choice.
    assert re.search(r'value="auto"\s+checked', html)


def test_setting_a_tier_reprices_at_once(client, db, seeded, default_profile):
    response = client.post(ROUTE, data={"tier": "complete"}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/property/HF-0001#kostenmodell"

    prop = _prop(db)
    assert prop.user_renovation_tier == "complete"
    cost = _cost(db, prop)
    assert cost.renovation_tier == "complete"
    assert "selbst gesetzt" in cost.assumptions[0]

    # The score for the live profile was written in the same request.
    profile_hash = default_profile.profile_hash
    score = db.scalar(
        select(Score).where(Score.property_id == prop.id, Score.profile_hash == profile_hash)
    )
    assert score is not None
    assert score.breakdown["cost"]["renovation_tier"] == "complete"


def test_dossier_shows_whose_tier_it_is(client, db, seeded):
    client.post(ROUTE, data={"tier": "light"})
    html = client.get("/property/HF-0001").text
    assert "<b>leicht</b>" in html
    assert "selbst gesetzt" in html
    # The listing said "fair" on a 1962 build: that is what was overridden.
    assert "Aus dem Inserat geschätzt:" in html
    assert re.search(r'value="light"\s+checked', html)


def test_a_higher_tier_costs_more(client, db, seeded):
    client.post(ROUTE, data={"tier": "light"})
    light = _cost(db, _prop(db)).total_mid
    client.post(ROUTE, data={"tier": "complete"})
    complete = _cost(db, _prop(db)).total_mid
    assert complete > light


def test_auto_clears_back_to_the_inference(client, db, seeded):
    client.post(ROUTE, data={"tier": "complete"})
    client.post(ROUTE, data={"tier": "auto"})
    prop = _prop(db)
    assert prop.user_renovation_tier is None
    cost = _cost(db, prop)
    assert cost.renovation_tier == "medium"
    assert "selbst gesetzt" not in cost.assumptions[0]
    html = client.get("/property/HF-0001").text
    assert "Aus dem Inserat geschätzt:" not in html


def test_the_inferred_tier_picked_explicitly_is_pinned(client, db, seeded):
    """Unlike a title typed back: a guess can move on the next crawl, the
    reader's judgement should not - and it counts as stated evidence."""
    client.post(ROUTE, data={"tier": "medium"})
    assert _prop(db).user_renovation_tier == "medium"


def test_an_unknown_tier_is_refused_not_read_as_auto(client, db, seeded):
    client.post(ROUTE, data={"tier": "complete"})
    response = client.post(ROUTE, data={"tier": "unknown"})
    assert response.status_code == 400
    assert "Nicht gespeichert" in response.text
    assert _prop(db).user_renovation_tier == "complete"

    bogus = client.post(ROUTE, data={"tier": "<b>x</b>"})
    assert bogus.status_code == 400
    assert "<b>x</b>" not in bogus.text


def test_unknown_property_is_404(client, seeded):
    assert client.post("/property/HF-9999/sanierungsstufe", data={"tier": "light"}).status_code == 404


def test_a_merged_away_row_writes_to_the_survivor(client, db, seeded):
    survivor = _prop(db)
    ghost = make_property(db, public_id="HF-0099", merged_into_id=survivor.id)
    response = client.post(
        f"/property/{ghost.public_id}/sanierungsstufe",
        data={"tier": "heavy"},
        follow_redirects=False,
    )
    assert response.headers["location"] == "/property/HF-0001#kostenmodell"
    assert _prop(db).user_renovation_tier == "heavy"
    assert _prop(db, "HF-0099").user_renovation_tier is None


def test_the_profile_in_the_query_string_is_kept(client, db, seeded):
    response = client.post(
        f"{ROUTE}?total_budget_max=900000", data={"tier": "heavy"}, follow_redirects=False
    )
    assert response.headers["location"] == (
        "/property/HF-0001?total_budget_max=900000#kostenmodell"
    )


def test_the_json_says_whose_tier_it_is(client, db, seeded):
    assert client.get("/api/property/HF-0001.json").json()["user_renovation_tier"] is None
    client.post(ROUTE, data={"tier": "heavy"})
    payload = client.get("/api/property/HF-0001.json").json()
    assert payload["user_renovation_tier"] == "heavy"
    assert payload["cost"]["renovation_tier"] == "heavy"


def test_a_property_never_scored_can_still_get_a_tier(client, db, seeded):
    """``/add`` stores but never scores, so a hand-added row has no estimate.
    Setting its tier is what computes the first one."""
    fresh = make_property(db, public_id="HF-0077")
    assert "Sanierungsstufe ändern" in client.get("/property/HF-0077").text
    client.post("/property/HF-0077/sanierungsstufe", data={"tier": "heavy"})
    assert _cost(db, fresh).renovation_tier == "heavy"


def test_a_stored_tier_the_model_ignores_is_not_shown_as_in_force(client, db, seeded):
    """A hand-edited or since-renamed value is priced as the listing's tier,
    so the page must say "Automatisch", not an override nobody applies."""
    prop = _prop(db)
    prop.user_renovation_tier = "unknown"
    db.commit()
    html = client.get("/property/HF-0001").text
    assert re.search(r'value="auto"\s+checked', html)
    assert "Aus dem Inserat geschätzt:" not in html
    assert "· selbst gesetzt</small>" not in html
