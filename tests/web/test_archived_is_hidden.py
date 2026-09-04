"""Archiving hides a property from every reader-facing view, and says so.

Hiding is the safe half of GitHub issue #9: the observation history keeps
growing (the database remembering is the product), but the radar, the exports,
the map and the digest stop showing the row. The one thing it may not do is
disappear quietly, so the status line always names how many are hidden.

``rejected`` is the other half of the collision that issue reported: it is a
judgement about the farm, and a judged farm stays visible.
"""

from __future__ import annotations

from sqlalchemy import select

from hofradar.db.models import Property


def _archive(db, public_id: str) -> None:
    prop = db.scalar(select(Property).where(Property.public_id == public_id))
    prop.user_state = "archived"
    db.commit()


def test_archived_is_gone_from_the_radar(client, db, seeded):
    assert "HF-0001" in client.get("/").text
    _archive(db, "HF-0001")
    assert "HF-0001" not in client.get("/").text


def test_archived_is_gone_from_the_json_api(client, db, seeded):
    _archive(db, "HF-0001")
    payload = client.get("/api/properties.json").json()
    assert "HF-0001" not in [row["public_id"] for row in payload["properties"]]


def test_archived_is_gone_from_the_csv_export_and_the_map(client, db, seeded):
    _archive(db, "HF-0001")
    assert "HF-0001" not in client.get("/api/export.csv").text
    assert "HF-0001" not in client.get("/map").text


def test_the_status_line_counts_what_it_hides(client, db, seeded):
    _archive(db, "HF-0001")
    html = client.get("/").text
    assert "1 archivierte ausgeblendet" in html


def test_include_hidden_brings_it_back(client, db, seeded):
    _archive(db, "HF-0001")
    assert "HF-0001" in client.get("/?include_hidden=1").text


def test_rejected_is_a_judgement_and_stays_visible(client, db, seeded):
    prop = db.scalar(select(Property).where(Property.public_id == "HF-0001"))
    prop.user_state = "rejected"
    db.commit()
    assert "HF-0001" in client.get("/").text


def test_archiving_keeps_the_observation_history(client, db, seeded):
    client.post("/property/HF-0001/triage", data={"user_state": "archived"})
    db.expire_all()
    prop = db.scalar(select(Property).where(Property.public_id == "HF-0001"))
    assert prop.user_state == "archived"
    assert len(prop.observations) == 2


def test_the_dossier_still_shows_an_archived_property(client, db, seeded):
    _archive(db, "HF-0001")
    response = client.get("/property/HF-0001")
    assert response.status_code == 200


def test_asking_for_archived_by_name_is_not_answered_with_nothing(client, db, seeded):
    """``user_state=archived`` is an explicit ask; the default hide may not veto it.

    Otherwise the two clauses AND into an unsatisfiable query and the API
    answers a confident, unexplained empty list - the silence this repo keeps
    producing.
    """
    _archive(db, "HF-0001")
    body = client.get("/api/properties.json?user_state=archived").json()
    assert [row["public_id"] for row in body["properties"]] == ["HF-0001"]
