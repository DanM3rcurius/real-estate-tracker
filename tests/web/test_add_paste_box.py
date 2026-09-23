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
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from hofradar.db.models import Document, Property
from tests.fixtures.pdf import make_pdf

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


#: A portal's bookmark widget, pasted whole - the page from issue #10. Every
#: fact on it is "k. A."; what makes it refusable is the shape of the page,
#: not how little was parsed out of it.
PASTED_MERKLISTE = """<!DOCTYPE html>
<html lang="de"><head><title>Merkliste - OVBimmo.de</title></head>
<body><h1>Merkliste</h1>
<p>Sie haben noch keine Objekte gemerkt.</p>
<p>Kaufpreis: k. A.</p>
<p>Wohnfläche: k. A.</p>
</body></html>
"""


def test_a_pasted_portal_page_is_refused_and_says_why(client: TestClient, db_session) -> None:
    """It saved, it got a public_id and it said nothing (issue #10). The paste
    box must explain the refusal instead - and leave no property behind."""
    with _offline():
        response = client.post("/add", data={"url": "", "text": PASTED_MERKLISTE})

    assert response.status_code == 200
    assert "kein Inserat" in response.text
    assert db_session.query(Property).count() == 0


# --------------------------------------------------------------------------- #
# The exposé PDF
# --------------------------------------------------------------------------- #

#: What a broker's exposé actually looks like: a cover line, a table of
#: labelled facts, prose on the next page.
PDF_LINES = [
    [
        "Hofstelle mit Stadel bei Vogtareuth",
        "Kaufpreis: 595.000 EUR",
        "Grundstueck: 8.000 m2",
        "Wohnflaeche: 240 m2",
        "Nutzflaeche: 320 m2",
        "Baujahr: 1891",
        "83569 Vogtareuth, Landkreis Rosenheim",
    ],
    ["Scheune, Stall und Tenne. Obstgarten. Verkauf aus Altersgruenden."],
]

#: The same object, priced differently on paper than the seller said on the
#: phone - the case that decides which of the two the listing keeps.
PDF_OTHER_PRICE_LINES = [
    [
        "Hofstelle mit Stadel bei Vogtareuth",
        "Kaufpreis: 640.000 EUR",
        "Nutzflaeche: 320 m2",
        "83569 Vogtareuth, Landkreis Rosenheim",
    ]
]


@pytest.fixture()
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Uploads must land in the test's own directory, never in the repo."""
    monkeypatch.setenv("HOFRADAR_DATA_DIR", str(tmp_path))
    return tmp_path


def _pdf_upload(data: bytes, name: str = "expose.pdf") -> dict[str, tuple[str, bytes, str]]:
    return {"pdf": (name, data, "application/pdf")}


def test_an_uploaded_expose_is_read_and_the_file_is_kept(
    client: TestClient, db_session, data_dir: Path
) -> None:
    """The PDF is the only thing the reader had, so it has to be the listing -
    and the file itself has to survive, because it is the evidence."""
    with _offline():
        response = client.post("/add", data={"url": "", "text": ""}, files=_pdf_upload(make_pdf(PDF_LINES)))

    assert response.status_code == 200, response.text[:400]

    prop = db_session.query(Property).one()
    assert prop.price == 595000.0
    assert prop.land_sqm == 8000.0
    assert prop.living_sqm == 240.0
    assert prop.year_built == 1891
    assert prop.town == "Vogtareuth"

    document = db_session.query(Document).one()
    assert document.property_id == prop.id
    assert document.kind == "upload"
    assert document.title == "expose.pdf"
    assert document.local_path is not None
    stored = Path(document.local_path)
    assert stored.exists()
    assert stored.parent == data_dir / "uploads"


def test_a_scanned_expose_says_so_and_saves_nothing(
    client: TestClient, db_session, data_dir: Path
) -> None:
    """No text layer and nothing else pasted: an empty property with a warning
    attached would be exactly the silence that looks like success."""
    with _offline():
        response = client.post(
            "/add", data={"url": "", "text": ""}, files=_pdf_upload(make_pdf([[]]), "scan.pdf")
        )

    assert response.status_code == 400
    assert "gescannt" in response.text
    assert "scan.pdf" in response.text
    assert db_session.query(Property).count() == 0


def test_a_scanned_expose_is_still_kept_when_the_text_was_pasted_too(
    client: TestClient, db_session, data_dir: Path
) -> None:
    """The pasted words carry the listing; the warning still reaches the page."""
    with _offline():
        response = client.post(
            "/add", data={"url": "", "text": PASTED}, files=_pdf_upload(make_pdf([[]]), "scan.pdf")
        )

    assert response.status_code == 200, response.text[:400]
    assert "gescannt" in response.text
    prop = db_session.query(Property).one()
    assert prop.price == 595000.0
    assert db_session.query(Document).count() == 1


def test_a_text_file_posing_as_a_pdf_is_refused_by_name(
    client: TestClient, db_session, data_dir: Path
) -> None:
    """The content type is what the browser guessed; the bytes are the truth."""
    with _offline():
        response = client.post(
            "/add",
            data={"url": "", "text": ""},
            files=_pdf_upload(b"Kaufpreis: 1 EUR\nkein PDF\n", "notizen.txt"),
        )

    assert response.status_code == 400
    assert "notizen.txt" in response.text
    assert db_session.query(Property).count() == 0
    assert not (data_dir / "uploads").exists()


def test_the_pasted_text_wins_and_the_pdf_fills_its_holes(
    client: TestClient, db_session, data_dir: Path
) -> None:
    """The human's own words beat the cover page, but a fact only the PDF has
    is still taken - otherwise uploading it would cost information."""
    with _offline():
        response = client.post(
            "/add",
            data={"url": "", "text": PASTED},
            files=_pdf_upload(make_pdf(PDF_OTHER_PRICE_LINES)),
        )

    assert response.status_code == 200, response.text[:400]
    prop = db_session.query(Property).one()
    assert prop.price == 595000.0  # pasted, not the PDF's 640.000
    assert prop.usable_sqm == 320.0  # only the PDF had it
    assert db_session.query(Document).one().kind == "upload"


def test_the_same_expose_uploaded_twice_is_one_property(
    client: TestClient, db_session, data_dir: Path
) -> None:
    """The file names itself by its digest, so a second upload updates the row
    it already has instead of inventing a twin."""
    pdf = make_pdf(PDF_LINES)
    with _offline():
        first = client.post("/add", data={"url": "", "text": ""}, files=_pdf_upload(pdf))
        second = client.post("/add", data={"url": "", "text": ""}, files=_pdf_upload(pdf))

    assert first.status_code == 200
    assert second.status_code == 200, second.text[:400]
    assert db_session.query(Property).count() == 1
    assert db_session.query(Document).count() == 1
    assert len(list((data_dir / "uploads").glob("*.pdf"))) == 1

#: A rental the regex cannot see: no rent vocabulary, a warm rent with no label.
SUBTLE_RENTAL = """Bauernhaus in Bad Aibling
Preis: 1.400 EUR
Wohnfläche: 120 m2
83043 Bad Aibling

Charmantes Bauernhaus, 1.400 warm, ab sofort frei."""

_JEV_ENDPOINT = "https://api.typesafe.ai/v1/systemone"


def _jev_says_rental() -> dict:
    from hofradar.triage.jev import Q_DWELLING, Q_OFFER, Q_SUBSTANCE

    return {
        "model": "jev-1.13.0",
        "answers": {
            Q_OFFER: {
                "type": "choice",
                "choice": "miete",
                "confidence": 0.8,
                "probabilities": {"kauf": 0.05, "miete": 0.94, "unklar": 0.01},
            },
            Q_DWELLING: {
                "type": "choice",
                "choice": "haus",
                "confidence": 0.7,
                "probabilities": {"hofstelle": 0.1, "haus": 0.9},
            },
            Q_SUBSTANCE: {"type": "noul", "noul": 0.1},
        },
        "usage": {"input_tokens": 100, "output_tokens": 20},
    }


def test_a_pasted_rental_gets_the_triage_verdict_and_is_still_saved(
    client: TestClient, db_session, monkeypatch
) -> None:
    """The paste box never drops what a human pasted; the verdict rides along
    as evidence and a warning, and the scoring gate does the rejecting."""
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    with respx.mock(assert_all_called=False) as mock:
        jev = mock.post(_JEV_ENDPOINT).mock(return_value=httpx.Response(200, json=_jev_says_rental()))
        mock.route(url__regex=_ANY_HTTP).mock(return_value=httpx.Response(200, json=[]))
        response = client.post("/add", data={"url": "", "text": SUBTLE_RENTAL})

    assert response.status_code == 200
    assert jev.call_count == 1
    assert "Mietangebot mit 94%" in response.text

    prop = db_session.query(Property).one()
    assert prop.evidence["triage"]["offer_kind"] == "miete"
    assert prop.evidence["triage"]["source"] == "jev"


def test_without_a_key_the_paste_box_asks_nothing(client: TestClient, monkeypatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with respx.mock(assert_all_called=False) as mock:
        jev = mock.post(_JEV_ENDPOINT)
        mock.route(url__regex=_ANY_HTTP).mock(return_value=httpx.Response(200, json=[]))
        response = client.post("/add", data={"url": "", "text": SUBTLE_RENTAL})

    assert response.status_code == 200
    assert not jev.called
    assert "Triage" not in response.text


def test_a_triage_outage_is_said_on_the_confirmation_page(
    client: TestClient, db_session, monkeypatch
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")
    with respx.mock(assert_all_called=False) as mock:
        mock.post(_JEV_ENDPOINT).mock(return_value=httpx.Response(503, text="down"))
        mock.route(url__regex=_ANY_HTTP).mock(return_value=httpx.Response(200, json=[]))
        response = client.post("/add", data={"url": "", "text": SUBTLE_RENTAL})

    assert response.status_code == 200
    assert "ohne Zweitmeinung" in response.text
    assert db_session.query(Property).count() == 1, "an outage must not lose the paste"


# --------------------------------------------------------------------------- #
# Issue #27: the exposé states the facts, the card said "k. A."
# --------------------------------------------------------------------------- #

#: What a browser puts on the clipboard when a reader selects a portal's
#: detail page: every table cell on its own line, labels without colons, a
#: soft hyphen the portal set for line breaking, and the "m²" superscript
#: copied as a plain "2".
PASTED_FROM_A_PORTAL = """Charmantes Bauernhaus mit großem Garten
83109 Großkarolinenfeld
Objektdaten
Zimmer

5
Grundstück

1.147 m2
Wohnfläche

140 m2
Kauf\u00adpreis

559.000,00 €
Baujahr

1995
Ehemalige Landwirtschaft mit Stadel und Obstgarten."""


def test_a_paste_copied_from_a_portal_page_keeps_its_facts(
    client: TestClient, db_session
) -> None:
    with _offline():
        response = client.post("/add", data={"url": "", "text": PASTED_FROM_A_PORTAL})

    assert response.status_code == 200
    prop = db_session.query(Property).one()
    assert prop.price == 559000.0
    assert prop.land_sqm == 1147.0
    assert prop.living_sqm == 140.0
    assert prop.rooms == 5
    assert prop.year_built == 1995
    assert "Eckdaten" not in response.text


#: A broker's exposé as pypdf reads a two-column fact table: label and value
#: on one line with no colon, a label above its value, and a price grouped by
#: a no-break space (WinAnsi, which make_pdf writes, has no narrow one).
#: Every one of these used to come out None.
PDF_TABLE_LINES = [
    [
        "Idyllische Hofstelle bei Vogtareuth",
        "83569 Vogtareuth",
        "Kaufpreis 1\u00a0250\u00a0000 €",
        "Wohnfläche ca. 310 m²",
        "Grundstücksfläche ca.",
        "7.112 m²",
        "Anzahl Zimmer 9",
    ],
    ["Scheune, Stall und Tenne. Obstgarten."],
]


def test_an_uploaded_expose_with_a_fact_table_keeps_its_facts(
    client: TestClient, db_session, data_dir: Path
) -> None:
    with _offline():
        response = client.post(
            "/add", data={"url": "", "text": ""}, files=_pdf_upload(make_pdf(PDF_TABLE_LINES))
        )

    assert response.status_code == 200, response.text[:400]
    prop = db_session.query(Property).one()
    assert prop.price == 1250000.0
    assert prop.living_sqm == 310.0
    assert prop.land_sqm == 7112.0
    assert prop.rooms == 9


def test_a_paste_without_its_areas_says_so_on_the_confirmation_page(
    client: TestClient,
) -> None:
    thin = "Sacherl bei Vogtareuth\n83569 Vogtareuth\nKaufpreis: 300.000 EUR"
    with _offline():
        response = client.post("/add", data={"url": "", "text": thin})

    assert "Eckdaten: kein Wert gefunden für Wohnfläche, Grundstücksfläche" in response.text
