"""What the paste box does with a pasted exposé.

The POST handler built its ``RawListing`` by hand - source key, URL, title,
description - and never asked the manual adapter to read the text. So every
labelled field the adapter knows how to parse (``Kaufpreis``, ``Wohnfläche``,
``Grundstück``, ``Baujahr``) arrived empty, on top of the unlabelled location
that issue #3 was filed about. The form saved something either way and said
nothing, which is what "it looked like it worked" meant.
"""

from __future__ import annotations

import re

import httpx
import respx
from fastapi.testclient import TestClient

from hofradar.db.models import Property

PASTED = """Sacherl mit Stadel in Alleinlage bei Vogtareuth
Kaufpreis: 595.000 EUR VB
Grundstück: 8.000 m2
Wohnfläche: 240 m2
Baujahr: 1891
83569 Vogtareuth, Landkreis Rosenheim

Ehemaliger Bauernhof. Scheune, Stall und Tenne. Obstgarten. Teilung moeglich.
Verkauf aus Altersgruenden, privat zu verkaufen, kein Makler."""

NO_LOCATION = """Schönes Sacherl in Alleinlage
Kaufpreis: 300.000 EUR
Wohnfläche: 180 m2"""

_ANY_HTTP = re.compile(r"^https?://.*")


def _offline():
    """Geocoding is not what these assert on; keep the whole test off the wire."""
    mock = respx.mock(assert_all_called=False)
    mock.route(url__regex=_ANY_HTTP).mock(return_value=httpx.Response(200, json=[]))
    return mock


def test_a_pasted_expose_is_actually_parsed(client: TestClient, db_session) -> None:
    with _offline():
        response = client.post("/add", data={"url": "", "text": PASTED})

    assert response.status_code == 200

    prop = db_session.query(Property).one()
    assert prop.price == 595000.0
    assert prop.land_sqm == 8000.0
    assert prop.living_sqm == 240.0
    assert prop.year_built == 1891
    assert prop.town == "Vogtareuth"
    assert prop.postcode == "83569"


def test_the_confirmation_page_shows_what_stayed_unclear(
    client: TestClient, db_session
) -> None:
    """A listing that can never be placed must say so on the page that saved it."""
    with _offline():
        response = client.post("/add", data={"url": "", "text": NO_LOCATION})

    assert response.status_code == 200
    body = response.text
    assert "unklar" in body
    assert "no location found" in body


def test_a_locatable_paste_does_not_cry_wolf(client: TestClient) -> None:
    with _offline():
        response = client.post("/add", data={"url": "", "text": PASTED})

    assert "no location found" not in response.text
