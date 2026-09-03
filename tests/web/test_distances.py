"""Air distance and driving distance are two different facts.

The failure this guards against is subtle and expensive: showing 23 km in a
"Fahrstrecke" field because that is what the straight line says, and sending
the user on a 45-minute drive they did not agree to. Unknown must read as
unknown, everywhere.
"""

from __future__ import annotations

import re

DRIVING_FIELD = re.compile(r'data-field="driving">([^<]*)<')


def driving_values(html: str) -> list[str]:
    return [value.strip() for value in DRIVING_FIELD.findall(html)]


def test_unknown_driving_distance_renders_nicht_geprueft(client, seeded):
    html = client.get("/api/results?air_km_max=40").text
    values = driving_values(html)
    assert values, "no driving field rendered"
    assert "nicht geprüft" in values


def test_driving_field_never_shows_the_air_distance(client, seeded):
    html = client.get("/api/results?air_km_max=40").text
    # HF-0001 is 23,4 km air and has no measured route.
    assert "23,4 km" in html, "air distance should still be shown in its own field"
    for value in driving_values(html):
        assert value != "23,4 km"
        assert value in ("nicht geprüft", "30,0 km")


def test_known_driving_distance_is_shown_as_itself(client, seeded):
    html = client.get("/api/results?air_km_max=200").text
    values = driving_values(html)
    assert "78,5 km" in values  # HF-0002 has a real route
    assert "nicht geprüft" in values  # HF-0001 does not


def test_dossier_marks_driving_distance_unchecked(client, seeded):
    html = client.get("/property/HF-0001").text
    assert "nicht geprüft" in html
    # The dossier fact table must not repeat the air value in the driving row.
    row = re.search(r"<th scope=\"row\">Fahrstrecke</th>\s*<td>([^<]*)</td>", html)
    assert row is not None
    assert row.group(1).strip() == "nicht geprüft"


def test_csv_export_leaves_unmeasured_route_empty(client, seeded):
    text = client.get("/api/export.csv?air_km_max=40").text
    header, *rows = [line for line in text.splitlines() if line.strip()]
    columns = header.lstrip("﻿").split(";")
    air_index = columns.index("Luftlinie km")
    drive_index = columns.index("Fahrstrecke km")
    for row in rows:
        cells = row.split(";")
        if cells[1] == "HF-0001":
            assert cells[air_index] == "23.4"
            assert cells[drive_index] == ""


def test_map_json_flags_unchecked_route(client, seeded):
    payload = client.get("/api/properties.json?air_km_max=200").json()
    for point in payload["properties"]:
        if point["distance_driving_km"] is None:
            assert point["distance_driving_checked"] is False


def test_map_distinguishes_imprecise_geocodes(client, seeded):
    html = client.get("/map?air_km_max=200").text
    # HF-0003 is only known to town level and must be marked as such.
    assert "Standort nur town" in html
    assert "Hohle Marker" in html
