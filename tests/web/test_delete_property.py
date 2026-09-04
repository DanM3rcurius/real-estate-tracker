"""The danger zone: a delete that has to be typed out before it happens.

Hiding is the default answer to "get this off my radar" (see
``test_archived_is_hidden``); this route is the narrow one for a mis-crawl or a
duplicate, and it is guarded by having to type the ``public_id`` back.
"""

from __future__ import annotations

from sqlalchemy import select

from hofradar.db.models import Observation, Property


def test_delete_removes_the_property_and_its_observations(client, db, seeded):
    response = client.post(
        "/property/HF-0001/delete",
        data={"confirm_public_id": "HF-0001"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/"

    db.expire_all()
    assert db.scalar(select(Property).where(Property.public_id == "HF-0001")) is None
    assert db.scalars(select(Observation)).all() != []  # other properties keep theirs
    assert "HF-0001" not in client.get("/").text


def test_an_htmx_delete_is_answered_with_a_redirect_header(client, db, seeded):
    """HTMX follows a 303 itself and would swap the radar into the dossier."""
    response = client.post(
        "/property/HF-0001/delete",
        data={"confirm_public_id": "HF-0001"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == "/"

    db.expire_all()
    assert db.scalar(select(Property).where(Property.public_id == "HF-0001")) is None


def test_delete_without_the_typed_id_changes_nothing(client, db, seeded):
    response = client.post("/property/HF-0001/delete", data={"confirm_public_id": ""})
    assert response.status_code == 400
    assert "HF-0001" in response.text

    db.expire_all()
    assert db.scalar(select(Property).where(Property.public_id == "HF-0001")) is not None


def test_delete_with_the_wrong_typed_id_changes_nothing(client, db, seeded):
    response = client.post(
        "/property/HF-0001/delete", data={"confirm_public_id": "HF-0002"}
    )
    assert response.status_code == 400

    db.expire_all()
    assert db.scalar(select(Property).where(Property.public_id == "HF-0001")) is not None


def test_deleting_a_merge_survivor_is_refused_with_a_german_message(client, db, seeded):
    survivor = db.scalar(select(Property).where(Property.public_id == "HF-0001"))
    duplicate = db.scalar(select(Property).where(Property.public_id == "HF-0002"))
    duplicate.merged_into_id = survivor.id
    db.commit()

    response = client.post(
        "/property/HF-0001/delete", data={"confirm_public_id": "HF-0001"}
    )
    assert response.status_code == 409
    assert "HF-0002" in response.text

    db.expire_all()
    assert db.scalar(select(Property).where(Property.public_id == "HF-0001")) is not None


def test_delete_of_an_unknown_property_is_a_404(client, seeded):
    response = client.post(
        "/property/HF-NOPE/delete", data={"confirm_public_id": "HF-NOPE"}
    )
    assert response.status_code == 404


def test_the_dossier_offers_the_danger_zone(client, seeded):
    html = client.get("/property/HF-0001").text
    assert "/property/HF-0001/delete" in html
    assert "confirm_public_id" in html
