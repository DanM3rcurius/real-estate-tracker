"""What the degraded notice tells the reader.

``lazy.call()`` catches everything so that a half-written sibling package
cannot 500 the UI. The cost is that a *database* failure arrives wearing the
same coat, and the notice used to announce a missing module for what was really
a schema one migration behind - which is how GitHub issue #7 was reported, and
why it was reported as the wrong thing.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import OperationalError

from hofradar.web.lazy import ModuleUnavailable, call_or

STALE_SCHEMA = OperationalError("SELECT sources.listing_ttl_days", {}, Exception())


def test_a_missing_module_still_says_so() -> None:
    error = ModuleUnavailable("hofradar.scoring", ImportError("no module"))

    assert not error.is_database_error
    assert "noch nicht verfügbar" in error.user_message
    assert "hofradar.scoring" in error.user_message


def test_a_stale_schema_names_the_database_not_the_module() -> None:
    error = ModuleUnavailable("hofradar.scoring:rescore_all", STALE_SCHEMA)

    assert error.is_database_error
    message = error.user_message
    assert "Datenbank" in message
    assert "hofradar migrate" in message
    # The old text sent the reader looking for a package that was never missing.
    assert "noch nicht verfügbar" not in message


def test_the_module_label_survives_in_the_database_message() -> None:
    """Which page failed is still worth knowing - it is just not the cause."""
    error = ModuleUnavailable("hofradar.scoring:rescore_all", STALE_SCHEMA)

    assert "Bewertung" in error.user_message


def test_call_or_degrades_a_database_error_into_the_new_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The path a page actually takes: fallback data plus a readable reason."""

    def _stale(*_args: object, **_kwargs: object) -> None:
        raise STALE_SCHEMA

    monkeypatch.setattr("hofradar.web.lazy.load", lambda _target: _stale)

    result, degraded = call_or("hofradar.scoring:rescore_all", "fallback")

    assert result == "fallback"
    assert degraded is not None
    assert "Datenbank" in degraded.message
    assert "hofradar migrate" in degraded.message
    assert "OperationalError" in (degraded.detail or "")


def test_a_genuinely_missing_module_is_not_reported_as_a_database_problem() -> None:
    with pytest.raises(ModuleUnavailable) as caught:
        from hofradar.web.lazy import call

        call("hofradar.web.no_such_module_xyz:nope")

    assert not caught.value.is_database_error
    assert "noch nicht verfügbar" in caught.value.user_message
