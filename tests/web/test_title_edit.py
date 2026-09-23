"""The reader renames a property; the listing's title stays the evidence.

Imported rows arrive with titles like "Exposé 4711 – Seite 1". The reader's
own name goes in ``user_title``, which ingest never writes, so it survives
every re-crawl - and wherever a reader sees a title, it is the one they gave.
"""

from __future__ import annotations

import csv
import html as htmllib
import io
import re

from sqlalchemy import select

from hofradar.db.models import Property
from hofradar.web.routes.dossier import USER_TITLE_MAX

HX = {"HX-Request": "true"}
LISTING_TITLE = "Hofstelle mit Stadel"


def _prop(db, public_id: str) -> Property:
    db.expire_all()
    return db.scalar(select(Property).where(Property.public_id == public_id))


def _prefilled(client, public_id: str) -> str:
    """The value the rename field actually offers, as a browser would send it."""
    page = client.get(f"/property/{public_id}").text
    match = re.search(r'<input type="text" name="title" value="([^"]*)"', page)
    assert match, "rename field not found"
    return htmllib.unescape(match.group(1))


def test_dossier_offers_the_rename_form(client, seeded):
    html = client.get("/property/HF-0001").text
    assert 'hx-post="/property/HF-0001/title"' in html
    assert "Titel ändern" in html
    # Nothing renamed yet: no "listing said" line and no reset button.
    assert "Im Inserat:" not in html
    assert "Titel aus dem Inserat verwenden" not in html


def test_rename_is_stored_beside_the_listing_title(client, db, seeded):
    response = client.post("/property/HF-0001/title", data={"title": "Moarhof"}, headers=HX)
    assert response.status_code == 200
    assert "<h1>Moarhof</h1>" in response.text
    assert f"Im Inserat: „{LISTING_TITLE}“" in response.text
    assert "Gespeichert" in response.text
    # htmx copies a <title> in the response into the browser tab.
    assert "<title>Moarhof –" in response.text

    prop = _prop(db, "HF-0001")
    assert prop.user_title == "Moarhof"
    assert prop.canonical_title == LISTING_TITLE


def test_plain_post_redirects_back_to_the_dossier(client, db, seeded):
    response = client.post(
        "/property/HF-0001/title", data={"title": "Moarhof"}, follow_redirects=False
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/property/HF-0001"
    assert _prop(db, "HF-0001").user_title == "Moarhof"


def test_whitespace_is_collapsed_to_one_line(client, db, seeded):
    client.post("/property/HF-0001/title", data={"title": "  Moarhof\n  bei\tAibling  "})
    assert _prop(db, "HF-0001").user_title == "Moarhof bei Aibling"


def test_empty_title_hands_the_heading_back_to_the_listing(client, db, seeded):
    client.post("/property/HF-0001/title", data={"title": "Moarhof"})
    response = client.post("/property/HF-0001/title", data={"title": "   "}, headers=HX)
    assert f"<h1>{LISTING_TITLE}</h1>" in response.text
    assert "Gespeichert: Titel aus dem Inserat" in response.text
    assert _prop(db, "HF-0001").user_title is None


def test_reset_button_clears_whatever_the_field_says(client, db, seeded):
    client.post("/property/HF-0001/title", data={"title": "Moarhof"})
    client.post("/property/HF-0001/title", data={"title": "Moarhof", "reset": "1"})
    assert _prop(db, "HF-0001").user_title is None


def test_typing_the_listing_title_back_stores_nothing(client, db, seeded):
    """Otherwise the heading would stop following the listing for no reason."""
    client.post("/property/HF-0001/title", data={"title": f" {LISTING_TITLE} "})
    assert _prop(db, "HF-0001").user_title is None


def test_saving_an_untouched_form_stores_nothing_even_for_ragged_titles(client, db, seeded):
    """A multi-line <h1> or a paste keeps newlines, tabs and double spaces.

    A text input cannot hold a newline - the browser deletes it and glues
    "Vierseithof" to "bei" - so the field must offer the title collapsed, and
    sending that back untouched must not freeze a copy as the reader's name.
    """
    prop = _prop(db, "HF-0001")
    prop.canonical_title = "Vierseithof\nbei  Rosenheim\t"
    db.commit()

    offered = _prefilled(client, "HF-0001")
    assert offered == "Vierseithof bei Rosenheim"
    client.post("/property/HF-0001/title", data={"title": offered})
    assert _prop(db, "HF-0001").user_title is None


def test_a_renamed_field_offers_the_readers_name(client, seeded):
    client.post("/property/HF-0001/title", data={"title": "Moarhof"})
    assert _prefilled(client, "HF-0001") == "Moarhof"


def test_a_merged_away_row_names_the_survivor(client, db, seeded):
    """Same rule as the Merkliste: a merged-away row is never shown in a list."""
    near, far = seeded["near"], seeded["far"]
    dropped = _prop(db, far.public_id)
    dropped.merged_into_id = near.id
    db.commit()

    response = client.post(
        f"/property/{far.public_id}/title", data={"title": "Moarhof"}, follow_redirects=False
    )
    assert response.headers["location"] == f"/property/{near.public_id}"
    assert _prop(db, near.public_id).user_title == "Moarhof"
    assert _prop(db, far.public_id).user_title is None


def test_htmx_on_a_merged_away_dossier_goes_to_the_survivor(client, db, seeded):
    """Swapping the survivor's heading into the dropped row's page would look
    saved and revert on reload; the page has to move to where the name lives."""
    near, far = seeded["near"], seeded["far"]
    dropped = _prop(db, far.public_id)
    dropped.merged_into_id = near.id
    db.commit()

    response = client.post(
        f"/property/{far.public_id}/title", data={"title": "Moarhof"}, headers=HX
    )
    assert response.status_code == 204
    assert response.headers["HX-Redirect"] == f"/property/{near.public_id}"
    assert _prop(db, near.public_id).user_title == "Moarhof"


def test_too_long_is_refused_not_cut_short(client, db, seeded):
    client.post("/property/HF-0001/title", data={"title": "Moarhof"})
    draft = "x" * (USER_TITLE_MAX + 1)

    response = client.post("/property/HF-0001/title", data={"title": draft}, headers=HX)
    assert response.status_code == 200
    assert "Nicht gespeichert" in response.text
    assert f'value="{draft}"' in response.text, "the reader's draft must not be thrown away"
    assert "<details class=\"fold title-edit\" open>" in response.text
    assert _prop(db, "HF-0001").user_title == "Moarhof"

    plain = client.post("/property/HF-0001/title", data={"title": draft})
    assert plain.status_code == 400
    assert _prop(db, "HF-0001").user_title == "Moarhof"


def test_exactly_the_limit_is_accepted(client, db, seeded):
    title = "y" * USER_TITLE_MAX
    client.post("/property/HF-0001/title", data={"title": title})
    assert _prop(db, "HF-0001").user_title == title


def test_unknown_property_is_404(client, seeded):
    assert client.post("/property/HF-9999/title", data={"title": "x"}).status_code == 404


def test_the_title_is_escaped(client, db, seeded):
    client.post("/property/HF-0001/title", data={"title": "<script>alert(1)</script>"})
    html = client.get("/property/HF-0001").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html


def test_renamed_dossier_page_uses_the_readers_name(client, seeded):
    client.post("/property/HF-0001/title", data={"title": "Moarhof"})
    html = client.get("/property/HF-0001").text
    assert "<title>Moarhof –" in html
    assert "<h1>Moarhof</h1>" in html
    assert f"Im Inserat: „{LISTING_TITLE}“" in html
    assert "Titel aus dem Inserat verwenden" in html
    # The facts table is the listing's evidence and keeps the listing's words.
    assert f"<td>{LISTING_TITLE}</td>" in html


def test_radar_card_json_and_csv_use_the_readers_name(client, seeded):
    client.post("/property/HF-0001/title", data={"title": "Moarhof"})

    assert "Moarhof" in client.get("/api/results").text

    rows = client.get("/api/properties.json").json()["properties"]
    near = next(row for row in rows if row["public_id"] == "HF-0001")
    assert near["title"] == "Moarhof"
    assert near["listing_title"] == LISTING_TITLE

    detail = client.get("/api/property/HF-0001.json").json()
    assert detail["title"] == "Moarhof"
    assert detail["user_title"] == "Moarhof"
    assert detail["listing_title"] == LISTING_TITLE

    exported = list(csv.reader(io.StringIO(client.get("/api/export.csv").text), delimiter=";"))
    title_col = exported[0].index("Titel")
    assert any(row[title_col] == "Moarhof" for row in exported[1:])


def test_search_box_finds_the_readers_name_and_the_listings(client, seeded):
    client.post("/property/HF-0001/title", data={"title": "Moarhof"})
    assert "HF-0001" in client.get("/api/results?q=moarhof").text
    assert "HF-0001" in client.get("/api/results?q=stadel").text
    # The control: a term nothing matches really does drop it.
    assert "HF-0001" not in client.get("/api/results?q=nirgendwo").text


def test_the_map_carries_a_renamed_title_as_data_not_markup(client, seeded):
    """Leaflet renders a string popup through innerHTML; titles come from
    third-party adverts and from the reader. The page hands them over as JSON
    data, and app.js escapes them before they reach the popup."""
    client.post("/property/HF-0001/title", data={"title": '<img src=x onerror="alert(1)">'})
    page = client.get("/map?air_km_max=200").text
    assert "<img src=x" not in page

    script = client.get("/static/app.js").text
    popup = script[script.index("marker.bindPopup(") :]
    popup = popup[: popup.index(");")]
    assert "esc(point.title)" in popup
    assert "esc(point.town" in popup
    assert "+ point.title" not in popup and "(point.title ||" not in popup
