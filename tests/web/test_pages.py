"""Every page must render - empty database included.

An empty database is the state the user sees on day one, so "it works once you
have data" is not good enough. These tests also walk every route with seeded
data, which is the cheapest way to catch a template that references a field
that no longer exists.
"""

from __future__ import annotations

import pytest

EMPTY_DB_PAGES = ["/healthz", "/", "/map", "/merkliste", "/settings", "/add", "/report", "/runs"]


@pytest.mark.parametrize("path", EMPTY_DB_PAGES)
def test_pages_render_on_empty_database(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text[:400]


def test_healthz_reports_counts(client, db, source):
    from tests.web.conftest import make_property

    payload = client.get("/healthz").json()
    assert payload["status"] == "ok"
    assert payload["properties"] == 0
    assert payload["last_run"] is None

    make_property(db, public_id="HF-9001")
    payload = client.get("/healthz").json()
    assert payload["properties"] == 1


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/map",
        "/merkliste",
        "/report",
        "/runs",
        "/add",
        "/settings",
        "/api/results",
        "/api/properties.json",
        "/api/export.csv",
        "/property/HF-0001",
        "/api/property/HF-0001.json",
    ],
)
def test_every_route_renders_with_data(client, seeded, path):
    response = client.get(path)
    assert response.status_code == 200, response.text[:400]


def test_unknown_property_is_404_not_500(client):
    response = client.get("/property/does-not-exist")
    assert response.status_code == 404
    assert "Kein Objekt" in response.text


def test_radar_shows_both_headline_sliders(client, seeded):
    html = client.get("/").text
    assert 'name="air_km_max"' in html
    assert 'name="total_budget_max"' in html
    assert "Entfernung" in html
    assert "Gesamtbudget" in html
    # The derived bands must be visible next to the sliders.
    assert "Fahrstrecke weich" in html
    assert "Kaufpreisziel" in html


def test_export_csv_respects_filters(client, seeded):
    wide = client.get("/api/export.csv?air_km_max=200&total_budget_max=3000000")
    narrow = client.get("/api/export.csv?air_km_max=40&total_budget_max=800000")
    assert wide.status_code == narrow.status_code == 200
    assert "HF-0002" in wide.text
    assert "HF-0002" not in narrow.text


def test_properties_json_keeps_air_and_driving_apart(client, seeded):
    payload = client.get("/api/properties.json?air_km_max=200").json()
    by_id = {p["public_id"]: p for p in payload["properties"]}
    near = by_id["HF-0001"]
    assert near["distance_air_km"] == pytest.approx(23.4)
    assert near["distance_driving_km"] is None
    assert near["distance_driving_checked"] is False
