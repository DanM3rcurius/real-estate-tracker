"""The radar remembers its sliders across navigation - in a cookie, via a 303.

The URL stays the truth: a bare ``/`` with a remembered state redirects to the
full query string, so permalinks, ``app.js``'s address-bar sync and the map
link all keep working unchanged.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hofradar.web.deps import FILTER_COOKIE


def test_a_filtered_request_sets_the_cookie(client, seeded):
    response = client.get("/?air_km_max=50&total_budget_max=700000&q=Traun")
    assert response.status_code == 200
    assert FILTER_COOKIE in response.cookies
    assert "air_km_max=50" in response.cookies[FILTER_COOKIE]
    assert "q=Traun" in response.cookies[FILTER_COOKIE]


def test_htmx_results_set_the_cookie_too(client, seeded):
    response = client.get("/api/results?air_km_max=45&total_budget_max=650000")
    assert FILTER_COOKIE in response.cookies


def test_bare_radar_redirects_to_the_remembered_state(client, seeded):
    client.get("/?air_km_max=50&total_budget_max=700000&q=Traun")
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/?")
    assert "air_km_max=50" in location and "q=Traun" in location


def test_bare_map_redirects_as_well(client, seeded):
    client.get("/?air_km_max=50&total_budget_max=700000")
    response = client.get("/map", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/map?")


def test_reset_clears_the_cookie_and_renders_defaults(client, seeded):
    client.get("/?air_km_max=50&total_budget_max=700000")
    response = client.get("/?reset=1", follow_redirects=False)
    assert response.status_code == 200
    # An expired cookie is a Set-Cookie with max-age=0 / empty value.
    assert client.cookies.get(FILTER_COOKIE) in (None, "")
    assert client.get("/", follow_redirects=False).status_code == 200


def test_a_garbage_cookie_is_ignored_not_looped(app, seeded):
    with TestClient(app) as fresh:
        fresh.cookies.set(FILTER_COOKIE, "<script>alert(1)</script>")
        response = fresh.get("/", follow_redirects=False)
        assert response.status_code == 200
    with TestClient(app) as fresh:
        fresh.cookies.set(FILTER_COOKIE, "x" * 5000)
        assert fresh.get("/", follow_redirects=False).status_code == 200


def test_explicit_parameters_beat_the_cookie(client, seeded):
    client.get("/?air_km_max=50&total_budget_max=700000")
    response = client.get("/?air_km_max=120", follow_redirects=False)
    assert response.status_code == 200
    assert 'value="120.0"' in response.text or 'value="120"' in response.text


def test_controls_offer_a_reset_link(client, seeded):
    assert 'href="/?reset=1"' in client.get("/").text
