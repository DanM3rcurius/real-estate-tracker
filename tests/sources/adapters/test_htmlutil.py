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
