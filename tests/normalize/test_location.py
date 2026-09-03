"""parse_location and LocationParts."""

from __future__ import annotations

from hofradar.normalize import LocationParts, parse_location


def test_postcode_and_town():
    result = parse_location("83620 Feldkirchen-Westerham")
    assert result == LocationParts(
        street=None, postcode="83620", town="Feldkirchen-Westerham", district=None
    )


def test_town_with_landkreis_in_parens():
    result = parse_location("Vogtareuth (Landkreis Rosenheim)")
    assert result.town == "Vogtareuth"
    assert result.district == "Rosenheim"
    assert result.postcode is None
    assert result.street is None


def test_colloquial_bei_reference():
    result = parse_location("Sacherl bei Bad Aibling")
    assert result.town == "Bad Aibling"
    assert result.street is None
    assert result.postcode is None


def test_bare_kreis_reference_with_state():
    result = parse_location("Rosenheim (Kreis), Bayern")
    assert result.town == "Rosenheim"
    assert result.district == "Rosenheim"


def test_full_street_address():
    result = parse_location("Musterstraße 12, 83024 Rosenheim")
    assert result.street == "Musterstraße 12"
    assert result.postcode == "83024"
    assert result.town == "Rosenheim"


def test_plain_town_only():
    result = parse_location("Aying")
    assert result.town == "Aying"
    assert result.postcode is None
    assert result.street is None
    assert result.district is None


def test_none_and_empty():
    assert parse_location(None) == LocationParts()
    assert parse_location("") == LocationParts()
    assert parse_location("   ") == LocationParts()


def test_kreis_abbreviation_lkr():
    result = parse_location("Aying (Lkr. Rosenheim)")
    assert result.town == "Aying"
    assert result.district == "Rosenheim"
