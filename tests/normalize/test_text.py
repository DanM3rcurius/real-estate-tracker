"""normalize_text / text_hash."""

from __future__ import annotations

import pytest

from hofradar.normalize import normalize_text, text_hash

NORMALIZE_CASES = [
    pytest.param("Bauernhof", "bauernhof", id="casefold"),
    pytest.param("BAUERNHOF", "bauernhof", id="casefold-upper"),
    pytest.param("Grundstück", "grundstueck", id="fold-u-umlaut"),
    pytest.param("Höfe", "hoefe", id="fold-o-umlaut"),
    pytest.param("Äcker", "aecker", id="fold-a-umlaut-upper"),
    pytest.param("Straße", "strasse", id="fold-eszett"),
    pytest.param("STRASSE", "strasse", id="already-ss"),
    pytest.param("Feldkirchen-Westerham", "feldkirchen westerham", id="hyphen-to-space"),
    pytest.param("83620  Feldkirchen", "83620 feldkirchen", id="collapse-whitespace"),
    pytest.param("Preis: 750.000 €!", "preis 750 000", id="strip-punctuation"),
    pytest.param("  spaced  ", "spaced", id="strip-outer-whitespace"),
    pytest.param(None, "", id="none"),
    pytest.param("", "", id="empty"),
]


@pytest.mark.parametrize("text,expected", NORMALIZE_CASES)
def test_normalize_text(text, expected):
    assert normalize_text(text) == expected


def test_normalize_text_is_idempotent():
    once = normalize_text("Straße München")
    assert normalize_text(once) == once


def test_text_hash_deterministic():
    assert text_hash("Bauernhof zu verkaufen") == text_hash("Bauernhof zu verkaufen")


def test_text_hash_insensitive_to_casing_and_umlauts():
    assert text_hash("Grundstück") == text_hash("GRUNDSTUECK")
    assert text_hash("Grundstück") == text_hash("grundstueck")


def test_text_hash_insensitive_to_whitespace_drift():
    assert text_hash("Bauernhof  zu   verkaufen") == text_hash("Bauernhof zu verkaufen")


def test_text_hash_differs_for_different_text():
    assert text_hash("Bauernhof") != text_hash("Resthof")


def test_text_hash_none_and_empty_are_consistent():
    assert text_hash(None) == text_hash("")


def test_text_hash_is_hex_string():
    digest = text_hash("some listing text")
    assert isinstance(digest, str)
    assert digest == digest.lower()
    int(digest, 16)  # raises ValueError if not valid hex
