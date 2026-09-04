"""An address nobody labelled is still an address.

``extract_labeled_fields`` reads ``Label: value`` lines, and an exposé a human
copies out of a paper writes the address as a bare line. It was therefore
dropped on the floor, and a dropped location is not a small loss: no town means
nothing to geocode, no geocode means neither distance, and the confidence gate
then holds the property off the shortlist and off the map. Nothing errored and
nothing warned - the listing simply never appeared (GitHub issue #3).
"""

from __future__ import annotations

import pytest

from hofradar.config import KeywordConfig
from hofradar.contracts import RawListing
from hofradar.normalize import normalize_listing
from hofradar.normalize.location import find_location_in_text, parse_location

#: The shape a human actually copies, from the issue's reproduction.
PASTED = """Sacherl mit Stadel in Alleinlage bei Vogtareuth
Kaufpreis: 595.000 EUR VB
Grundstück: 8.000 m2
Wohnfläche: 240 m2
Baujahr: 1891
83569 Vogtareuth, Landkreis Rosenheim

Ehemaliger Bauernhof. Scheune, Stall und Tenne. Obstgarten. Teilung moeglich.
Verkauf aus Altersgruenden, privat zu verkaufen, kein Makler."""


def _normalize(**kwargs: object) -> object:
    raw = RawListing(source_key="manual", url="manual:test", **kwargs)
    return normalize_listing(raw, KeywordConfig())


# --------------------------------------------------------------------------
# finding it
# --------------------------------------------------------------------------


def test_the_bare_postcode_line_is_found() -> None:
    assert find_location_in_text(PASTED) == "83569 Vogtareuth, Landkreis Rosenheim"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Das Anwesen liegt in 83620 Feldkirchen-Westerham und ist frei.", "83620 Feldkirchen-Westerham"),
        ("Hof in 83043 Bad Aibling", "83043 Bad Aibling"),
        ("83022 Rosenheim", "83022 Rosenheim"),
    ],
)
def test_prose_and_hyphenated_and_two_word_towns(text: str, expected: str) -> None:
    """RSS and sitemap detail pages carry the address in prose, not a field."""
    assert find_location_in_text(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Kaufpreis 95000 EUR, Baujahr 1891",  # five digits, but no town after it
        "Grundstück: 8.000 m2, 12345 Euro pro Jahr",  # 'Euro' is not a town
        "Wohnfläche 24000 m2",
        "Telefon 08031 123456",
        "Ein Text ganz ohne Ort.",
        "",
        None,
    ],
)
def test_a_five_digit_run_is_not_a_postcode_on_its_own(text: str | None) -> None:
    """A wrong town geocodes to a real place elsewhere and nothing catches it.

    So the shape has to be postcode *plus* town, never any five-digit run.
    """
    assert find_location_in_text(text) is None


def test_what_it_returns_is_something_parse_location_can_read() -> None:
    parts = parse_location(find_location_in_text(PASTED))

    assert parts.postcode == "83569"
    assert parts.town == "Vogtareuth"


# --------------------------------------------------------------------------
# using it
# --------------------------------------------------------------------------


def test_an_unlabelled_line_populates_postcode_and_town() -> None:
    listing = _normalize(title="Sacherl", description=PASTED)

    assert listing.postcode == "83569"
    assert listing.town == "Vogtareuth"


def test_the_labelled_form_still_wins() -> None:
    """A source that says where it is must not be second-guessed by prose."""
    listing = _normalize(
        description="Irgendwo steht 83569 Vogtareuth im Fließtext.",
        location_raw="83022 Rosenheim",
    )

    assert listing.town == "Rosenheim"
    assert listing.postcode == "83022"


def test_an_explicit_town_field_still_wins() -> None:
    listing = _normalize(description=PASTED, town="Bad Aibling")

    assert listing.town == "Bad Aibling"


def test_a_recovered_location_says_that_it_was_recovered() -> None:
    """Inferred is not the same as stated, and the record should show which."""
    listing = _normalize(description=PASTED)

    assert any("out of the description" in w for w in listing.warnings)
    evidence = listing.evidence["town"]
    assert evidence["quote"] == "83569 Vogtareuth, Landkreis Rosenheim"
    assert evidence["confidence"] < 0.8


def test_no_location_at_all_is_a_visible_warning_not_a_silent_none() -> None:
    """The complaint was 'it looked like it worked', not 'parsing is wrong'."""
    listing = _normalize(title="Sacherl", description="Schönes Objekt, Preis auf Anfrage.")

    assert listing.town is None
    assert any("no location found" in w for w in listing.warnings)


def test_a_listing_that_has_a_town_does_not_warn_about_one() -> None:
    listing = _normalize(description="Ein Hof.", location_raw="83022 Rosenheim")

    assert not any("location" in w or "town" in w for w in listing.warnings)
