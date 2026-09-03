"""The password gate.

The point of these tests is that the gate cannot be walked around: not by
guessing a cookie, not by calling the JSON API directly, not by asking healthz
what the database contains, and not by feeding the login form a redirect to
somebody else's site.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from hofradar.web import auth
from hofradar.web.app import create_app

PASSWORD = "ein-sicheres-passwort"


@pytest.fixture(autouse=True)
def _clean_auth_state(monkeypatch, tmp_path):
    """Each test gets its own signing key and an empty throttle."""
    auth.reset_throttle()
    monkeypatch.setenv("HOFRADAR_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HOFRADAR_PASSWORD", raising=False)
    monkeypatch.delenv("HOFRADAR_PASSWORD_HASH", raising=False)
    monkeypatch.delenv("HOFRADAR_SECRET_KEY", raising=False)
    yield
    auth.reset_throttle()


@pytest.fixture
def guarded_client(engine, monkeypatch) -> TestClient:
    monkeypatch.setenv("HOFRADAR_PASSWORD", PASSWORD)
    return TestClient(create_app(engine=engine, create_tables=True), follow_redirects=False)


@pytest.fixture
def open_client(engine) -> TestClient:
    return TestClient(create_app(engine=engine, create_tables=True), follow_redirects=False)


# --------------------------------------------------------------------------- #
# The gate is opt-in
# --------------------------------------------------------------------------- #


def test_without_a_password_nothing_changes(open_client: TestClient) -> None:
    assert open_client.get("/").status_code == 200
    assert open_client.get("/login").status_code == 404


def test_with_a_password_the_radar_redirects_to_login(guarded_client: TestClient) -> None:
    response = guarded_client.get("/")
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_the_login_page_itself_is_reachable(guarded_client: TestClient) -> None:
    response = guarded_client.get("/login")
    assert response.status_code == 200
    assert "Passwort" in response.text


def test_static_files_stay_public(guarded_client: TestClient) -> None:
    # The login page has to be able to load its own stylesheet.
    assert guarded_client.get("/static/app.css").status_code == 200


# --------------------------------------------------------------------------- #
# Signing in
# --------------------------------------------------------------------------- #


def test_the_right_password_lets_you_in(guarded_client: TestClient) -> None:
    response = guarded_client.post("/login", data={"password": PASSWORD, "next": "/"})
    assert response.status_code == 303
    assert auth.COOKIE_NAME in response.cookies

    landed = guarded_client.get("/")
    assert landed.status_code == 200


def test_the_wrong_password_does_not(guarded_client: TestClient) -> None:
    response = guarded_client.post("/login", data={"password": "falsch", "next": "/"})
    assert response.status_code == 401
    assert auth.COOKIE_NAME not in response.cookies
    assert "Falsches Passwort" in response.text


def test_logout_clears_the_session(guarded_client: TestClient) -> None:
    guarded_client.post("/login", data={"password": PASSWORD, "next": "/"})
    assert guarded_client.get("/").status_code == 200

    guarded_client.post("/logout")
    assert guarded_client.get("/").status_code == 303


# --------------------------------------------------------------------------- #
# Cookies cannot be forged or replayed
# --------------------------------------------------------------------------- #


def test_a_made_up_cookie_is_rejected(guarded_client: TestClient) -> None:
    guarded_client.cookies.set(auth.COOKIE_NAME, "v1.99999999999.deadbeef")
    assert guarded_client.get("/").status_code == 303


def test_an_expired_token_is_rejected() -> None:
    token = auth.make_token(now=time.time() - 10_000, max_age=1)
    assert auth.validate_token(token) is False


def test_a_valid_token_is_accepted() -> None:
    assert auth.validate_token(auth.make_token()) is True


def test_tampering_with_the_expiry_invalidates_the_signature() -> None:
    version, expiry, signature = auth.make_token().split(".", 2)
    forged = f"{version}.{int(expiry) + 86_400}.{signature}"
    assert auth.validate_token(forged) is False


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #


def test_a_pbkdf2_hash_is_accepted(monkeypatch) -> None:
    monkeypatch.delenv("HOFRADAR_PASSWORD", raising=False)
    monkeypatch.setenv("HOFRADAR_PASSWORD_HASH", auth.hash_password(PASSWORD))

    assert auth.verify_password(PASSWORD) is True
    assert auth.verify_password("falsch") is False


def test_a_malformed_hash_refuses_every_password(monkeypatch) -> None:
    monkeypatch.delenv("HOFRADAR_PASSWORD", raising=False)
    monkeypatch.setenv("HOFRADAR_PASSWORD_HASH", "not-a-hash")

    assert auth.verify_password(PASSWORD) is False
    assert auth.verify_password("") is False


def test_no_password_configured_verifies_nothing(monkeypatch) -> None:
    assert auth.password_configured() is False
    assert auth.verify_password("") is False
    assert auth.verify_password(PASSWORD) is False


# --------------------------------------------------------------------------- #
# API and HTMX callers get data-shaped answers, not an HTML page
# --------------------------------------------------------------------------- #


def test_an_anonymous_api_call_gets_401_not_a_login_page(guarded_client: TestClient) -> None:
    response = guarded_client.get("/api/properties.json")
    assert response.status_code == 401
    assert response.headers["hx-redirect"] == "/login"


def test_an_anonymous_htmx_fetch_is_told_to_redirect(guarded_client: TestClient) -> None:
    response = guarded_client.get("/api/results", headers={"HX-Request": "true"})
    assert response.status_code == 401
    assert response.headers["hx-redirect"] == "/login"


def test_healthz_stays_reachable_but_says_nothing(guarded_client: TestClient) -> None:
    response = guarded_client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_healthz_is_detailed_once_signed_in(guarded_client: TestClient) -> None:
    guarded_client.post("/login", data={"password": PASSWORD, "next": "/"})
    payload = guarded_client.get("/healthz").json()
    assert "properties" in payload


# --------------------------------------------------------------------------- #
# Throttling and open redirects
# --------------------------------------------------------------------------- #


def test_repeated_failures_lock_the_client_out(guarded_client: TestClient) -> None:
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        guarded_client.post("/login", data={"password": "falsch", "next": "/"})

    locked = guarded_client.post("/login", data={"password": "falsch", "next": "/"})
    assert locked.status_code == 429
    assert "Fehlversuche" in locked.text

    # Even the correct password waits out the lockout.
    still_locked = guarded_client.post("/login", data={"password": PASSWORD, "next": "/"})
    assert still_locked.status_code == 429


def test_a_successful_login_clears_the_failure_count() -> None:
    key = "203.0.113.7"
    for _ in range(auth.MAX_FAILED_ATTEMPTS - 1):
        auth.record_failure(key)
    assert auth.locked_out_for(key) == 0

    auth.clear_failures(key)

    # The budget is full again, so the next near-miss run does not lock out.
    for _ in range(auth.MAX_FAILED_ATTEMPTS - 1):
        auth.record_failure(key)
    assert auth.locked_out_for(key) == 0


def test_failures_outside_the_window_are_forgotten() -> None:
    key = "203.0.113.8"
    stale = time.time() - auth.ATTEMPT_WINDOW_SECONDS - 60
    for _ in range(auth.MAX_FAILED_ATTEMPTS):
        auth.record_failure(key, now=stale)

    assert auth.locked_out_for(key) == 0


def test_the_forwarded_client_is_used_for_throttling_behind_a_proxy() -> None:
    class _Request:
        headers = {"x-forwarded-for": "198.51.100.9, 10.0.0.1"}
        client = None

    assert auth.client_key(_Request()) == "198.51.100.9"


@pytest.mark.parametrize(
    "candidate",
    ["https://evil.example/steal", "//evil.example", "http://evil.example", None, ""],
)
def test_an_external_next_target_is_refused(candidate: str | None) -> None:
    assert auth.safe_next_path(candidate) == "/"


def test_an_internal_next_target_survives() -> None:
    assert auth.safe_next_path("/property/hof-1?x=1") == "/property/hof-1?x=1"


def test_the_login_page_is_never_a_next_target() -> None:
    assert auth.safe_next_path("/login") == "/"


def test_the_next_target_is_honoured_after_signing_in(guarded_client: TestClient) -> None:
    response = guarded_client.get("/map")
    assert response.status_code == 303
    assert "next=%2Fmap" in response.headers["location"]

    signed_in = guarded_client.post("/login", data={"password": PASSWORD, "next": "/map"})
    assert signed_in.headers["location"] == "/map"
