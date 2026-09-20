"""Focused coverage for ``extract_labeled_fields``'s parenthetical-suffix handling.

``living_sqm`` feeds the cost model's per-m^2 renovation band and a scoring
gate keys off it directly, so a label that silently fails to match is not
cosmetic - it is the difference between a property being rankable at all and
not. BLfD's owner-written exposés routinely qualify an area label with which
part of the Hofstelle it covers ("Wohnfläche (Bauernhaus)", "Nutzfläche
(Wirtschaftsteil)"), and this is shared code every HTML adapter uses, so the
fix and its guard rail both live here rather than in one adapter.
"""

from __future__ import annotations

from hofradar.sources.adapters._htmlutil import extract_labeled_fields


def test_rent_labels_keep_the_label_in_the_value():
    """"1.250 €" alone would parse as an asking price; the label is the fact."""
    fields = extract_labeled_fields("Kaltmiete: 1.250 €\nWohnfläche: 65 m²")
    assert fields["price_raw"] == "Kaltmiete: 1.250 €"
    assert fields["living_raw"] == "65 m²"


def test_sale_labels_are_unchanged_by_the_rent_rule():
    fields = extract_labeled_fields("Kaufpreis: 750.000 €")
    assert fields["price_raw"] == "750.000 €"


def test_a_parenthetical_suffixed_known_label_matches_its_base_field() -> None:
    fields = extract_labeled_fields("Wohnfläche (Bauernhaus): ca. 110 m²")
    assert fields == {"living_raw": "ca. 110 m²"}


def test_a_label_with_no_parenthetical_still_matches_as_before() -> None:
    fields = extract_labeled_fields("Wohnfläche: 180 m²")
    assert fields == {"living_raw": "180 m²"}


def test_an_unknown_label_with_a_parenthetical_still_does_not_match() -> None:
    # The normalisation strips a trailing "(...)" only to retry the lookup -
    # it must never turn into a fuzzy matcher for labels the map has never
    # heard of in any form, qualified or not.
    fields = extract_labeled_fields("Frobnitz (Bauernhaus): 42")
    assert fields == {}


def test_grundstuecksflaeche_still_matches_after_the_change() -> None:
    # Regression guard: this label matched exactly before the parenthetical
    # normalisation was added and must keep matching unchanged.
    fields = extract_labeled_fields("Grundstücksfläche: ca. 435 m²")
    assert fields == {"land_raw": "ca. 435 m²"}


def test_a_stripped_label_never_lands_on_a_different_known_field() -> None:
    # "Zimmer (Wohnfläche)" must resolve to its own base field, rooms_raw -
    # never bleed onto living_raw just because "Wohnfläche" is also a known
    # label. No two distinct known labels may collapse onto each other via
    # the parenthetical strip.
    fields = extract_labeled_fields("Zimmer (Wohnfläche): 4")
    assert fields == {"rooms_raw": "4"}


def test_malformed_parentheticals_never_match() -> None:
    # None of these are a known label with a trailing balanced qualifier, so
    # none may match: a parenthetical that splits the word itself, one with
    # no closing paren, one with nothing but a parenthetical, and - the
    # discriminating case - one with a parenthetical in the *middle* of an
    # otherwise-known label. That last one is what actually distinguishes the
    # anchored regex (r"\s*\([^)]*\)\s*$") from an unanchored r"\([^)]*\)":
    # unanchored, "wohn(x)fläche" becomes "wohnfläche" by deleting the
    # matched span wherever it sits, not just at the end, so it would wrongly
    # match living_raw. The anchored regex only ever strips a parenthetical
    # that ends the string, so it leaves "wohn(x)fläche" alone.
    assert extract_labeled_fields("Wohn(fläche): 5") == {}
    assert extract_labeled_fields("Wohnfläche (unbalanced: 12") == {}
    assert extract_labeled_fields("(Wohnfläche): 9") == {}
    assert extract_labeled_fields("Wohn(x)fläche: 5") == {}


# --------------------------------------------------------------------------- #
# Multi-fact lines: a tab or 2+ spaces separates two "Label: value" segments
# --------------------------------------------------------------------------- #


def test_two_labelled_facts_on_one_line_both_match() -> None:
    # The BLfD exposé template's signature shape (a tab or run of spaces
    # between two facts, never a single space - "Kaufpreis: 500.000, - EUR"
    # is one value with spaces in it).
    fields = extract_labeled_fields(
        "Wohnfläche: 110 m²          Grundstücksfläche: 800 m²"
    )
    assert fields == {"living_raw": "110 m²", "land_raw": "800 m²"}


def test_double_space_after_the_colon_is_still_one_field() -> None:
    fields = extract_labeled_fields("Kaufpreis:  59.000 €")
    assert fields == {"price_raw": "59.000 €"}


def test_double_space_inside_the_label_still_matches_living_raw() -> None:
    fields = extract_labeled_fields("Wohnfläche  (Bauernhaus): 110 m²")
    assert fields == {"living_raw": "110 m²"}


def test_prose_then_a_known_label_after_a_double_space_takes_the_labels_value() -> None:
    # The leading prose has no colon, so it is not itself a fact - it must
    # not swallow the real "Kaufpreis" value that follows it on the line.
    fields = extract_labeled_fields(
        "Traumhafte Hofstelle mit viel Charme  Kaufpreis: 400.000 €"
    )
    assert fields == {"price_raw": "400.000 €"}


# --------------------------------------------------------------------------- #
# Label on its own line, value on the next - numeric fields only
# --------------------------------------------------------------------------- #


def test_label_on_its_own_line_with_the_value_on_the_next() -> None:
    assert extract_labeled_fields("Wohnfläche\n~118 m²") == {"living_raw": "~118 m²"}
    assert extract_labeled_fields("Baujahr\n1837") == {"year_raw": "1837"}
    assert extract_labeled_fields("Preis\nauf Anfrage") == {"price_raw": "auf Anfrage"}


def test_rent_label_on_its_own_line_keeps_the_label_in_the_value() -> None:
    fields = extract_labeled_fields("Kaltmiete\n1.250 €")
    assert fields == {"price_raw": "Kaltmiete: 1.250 €"}


def test_next_line_value_not_taken_when_it_is_long_prose() -> None:
    fields = extract_labeled_fields(
        "Wohnfläche\nDies ist ein sehr langer Beschreibungstext der eindeutig Prosa ist"
    )
    assert fields == {}


def test_next_line_value_not_taken_when_it_contains_a_colon() -> None:
    fields = extract_labeled_fields("Wohnfläche\nca: 118 m²")
    assert fields == {}


def test_next_line_value_not_taken_when_it_has_no_digit() -> None:
    fields = extract_labeled_fields("Wohnfläche\nunbekannt")
    assert fields == {}


def test_next_line_value_not_taken_for_location_labels() -> None:
    # Lage/Ort are excluded on purpose: a "Lage" heading is followed by prose
    # on every exposé, and reading that prose as a location would block the
    # normaliser's own address recovery (decision 18).
    assert extract_labeled_fields("Lage\nRosenheim, sehr ruhig gelegen") == {}
    assert extract_labeled_fields("Ort\nRosenheim") == {}


def test_a_blank_line_between_label_and_value_is_not_bridged() -> None:
    assert extract_labeled_fields("Wohnfläche\n\n118 m²") == {}


# --------------------------------------------------------------------------- #
# Bare room count on a short line of its own
# --------------------------------------------------------------------------- #


def test_bare_room_count_on_a_short_line() -> None:
    assert extract_labeled_fields("28 Zimmer") == {"rooms_raw": "28"}
    assert extract_labeled_fields("6 Zi.") == {"rooms_raw": "6"}


def test_bare_room_count_inside_long_prose_is_not_taken() -> None:
    fields = extract_labeled_fields(
        "Die 3-Zimmer-Wohnung im DG ist vermietet und wird derzeit verwaltet."
    )
    assert fields == {}


def test_a_four_digit_number_before_zimmer_is_not_a_room_count() -> None:
    assert extract_labeled_fields("1984 Zimmer") == {}


def test_an_explicit_zimmer_label_beats_a_later_bare_room_count() -> None:
    fields = extract_labeled_fields("Zimmer: 4\n7 Zimmer")
    assert fields == {"rooms_raw": "4"}
