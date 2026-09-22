"""An uploaded exposé has to open when the reader clicks it.

``/add`` writes the PDF to disk and names the listing ``upload:<digest>`` -
the file's *identity*, not an address. That identity went straight into the
dossier's href, so "Inserat öffnen", "Dokument" and the source link were all
buttons that did nothing when clicked: the browser has no ``upload:`` scheme
and no route served the stored file. Same shape for a text-only paste, which
is named ``manual:<timestamp>``.
"""

from __future__ import annotations

import re

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy import select

from hofradar.db.models import Document, Property
from hofradar.web.query import NO_LINK_MANUAL
from tests.fixtures.pdf import make_pdf

_ANY_HTTP = re.compile(r"^https?://.*")

EXPOSE_LINES = [
    "Hofstelle mit Stadel in Alleinlage",
    "Kaufpreis: 495.000 EUR",
    "Grundstueck: 9.000 m2",
    "Wohnflaeche: 220 m2",
    "Baujahr: 1901",
    "83569 Vogtareuth, Landkreis Rosenheim",
    "Ehemaliger Bauernhof mit Scheune, Stall und Tenne.",
]

PASTED = """Sacherl mit Stadel in Alleinlage bei Vogtareuth
Kaufpreis: 595.000 EUR VB
Grundstück: 8.000 m2
Wohnfläche: 240 m2
Baujahr: 1891
83569 Vogtareuth, Landkreis Rosenheim"""


def _offline():
    mock = respx.mock(assert_all_called=False)
    mock.route(url__regex=_ANY_HTTP).mock(return_value=httpx.Response(200, json=[]))
    return mock


@pytest.fixture()
def uploaded(client: TestClient, db_session, tmp_path, monkeypatch):
    """One property whose only source is an uploaded PDF."""
    monkeypatch.setenv("HOFRADAR_DATA_DIR", str(tmp_path))
    with _offline():
        response = client.post(
            "/add", files={"pdf": ("expose.pdf", make_pdf([EXPOSE_LINES]), "application/pdf")}
        )
    assert response.status_code == 200
    prop = db_session.scalars(select(Property)).unique().one()
    document = db_session.scalars(select(Document)).one()
    return prop, document


def test_the_stored_pdf_is_served_inline(client: TestClient, uploaded) -> None:
    _prop, document = uploaded
    response = client.get(f"/document/{document.id}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith("inline")
    assert response.content.startswith(b"%PDF")


def test_the_dossier_links_to_the_file_and_never_to_the_identity(
    client: TestClient, uploaded
) -> None:
    prop, document = uploaded
    body = client.get(f"/property/{prop.public_id}").text

    assert f'href="/document/{document.id}"' in body
    # The pseudo-URL may be *printed* (it is the listing's identity), but it
    # must never be something the page asks a browser to follow.
    assert 'href="upload:' not in body


def test_a_text_only_paste_says_why_there_is_no_link(client: TestClient, db_session) -> None:
    with _offline():
        client.post("/add", data={"url": "", "text": PASTED})
    prop = db_session.scalars(select(Property)).unique().one()

    body = client.get(f"/property/{prop.public_id}").text
    assert 'href="manual:' not in body
    assert NO_LINK_MANUAL in body


def test_a_real_listing_url_is_still_a_link(client, db, seeded) -> None:
    """The seeded fixture's https source must keep behaving exactly as before."""
    body = client.get("/property/HF-0001").text
    assert 'href="https://example.invalid/HF-0001"' in body
    assert "Inserat öffnen" in body


def test_an_unknown_document_is_404(client: TestClient, seeded) -> None:
    assert client.get("/document/9999").status_code == 404


def test_a_vanished_file_says_so_rather_than_404ing_silently(
    client: TestClient, db_session, uploaded, tmp_path
) -> None:
    _prop, document = uploaded
    (tmp_path / "uploads" / f"{document.local_path.rsplit('/', 1)[-1]}").unlink()

    response = client.get(f"/document/{document.id}")
    assert response.status_code == 410
    assert "nicht mehr vorhanden" in response.text


def test_a_path_outside_the_uploads_directory_is_refused(
    client: TestClient, db_session, uploaded, tmp_path
) -> None:
    """``local_path`` is a plain string column; the route decides what it serves."""
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF-1.4 not yours")
    _prop, document = uploaded
    document.local_path = str(outside)
    db_session.commit()

    response = client.get(f"/document/{document.id}")
    assert response.status_code == 410
    assert b"not yours" not in response.content
