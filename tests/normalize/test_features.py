"""extract_features / classify_property_type, including the word-boundary guards."""

from __future__ import annotations

from hofradar.config import KeywordConfig, load_keywords
from hofradar.normalize import classify_property_type, extract_features

REAL_KEYWORDS = load_keywords()


def test_outbuildings_from_buildings_group():
    features = extract_features("Das Anwesen hat eine Scheune und eine Tenne.", REAL_KEYWORDS)
    assert "scheune" in features.outbuildings
    assert "tenne" in features.outbuildings


def test_word_boundary_stall_does_not_fire_inside_installation():
    # "Stall" is a real keywords.buildings term; "Installation" contains the
    # letters s-t-a-l-l but not as a standalone word.
    features = extract_features("Neue Installation der Heizungsanlage.", REAL_KEYWORDS)
    assert "stall" not in features.outbuildings


def test_word_boundary_stall_fires_as_standalone_word():
    features = extract_features("Das Grundstück hat einen Stall.", REAL_KEYWORDS)
    assert "stall" in features.outbuildings


def test_short_ambiguous_term_requires_capitalised_context():
    keywords = KeywordConfig(buildings=["Gut"])
    lowercase_adjective = extract_features("Das Haus ist gut erhalten.", keywords)
    assert "gut" not in lowercase_adjective.outbuildings

    proper_noun_usage = extract_features("Das Gut liegt idyllisch.", keywords)
    assert "gut" in proper_noun_usage.outbuildings


def test_exclusion_flags_from_negative_group():
    features = extract_features("Diese Eigentumswohnung ist ein Neubau.", REAL_KEYWORDS)
    assert "eigentumswohnung" in features.exclusion_flags
    assert "neubau" in features.exclusion_flags


def test_hidden_signals_canonical_slugs():
    text = "Von privat, kein Makler, provisionsfrei. Verkauf aus Altersgründen. Chiffre 123."
    features = extract_features(text, REAL_KEYWORDS)
    assert "privatverkauf" in features.hidden_signals
    assert "kein_makler" in features.hidden_signals
    assert "provisionsfrei" in features.hidden_signals
    assert "aus_altersgruenden" in features.hidden_signals
    assert "chiffre" in features.hidden_signals


def test_hidden_signal_vb():
    features = extract_features("Preis: 750.000 € VB", REAL_KEYWORDS)
    assert "vb" in features.hidden_signals


def test_hidden_signal_preis_auf_anfrage():
    features = extract_features("Preis auf Anfrage.", REAL_KEYWORDS)
    assert "preis_auf_anfrage" in features.hidden_signals


def test_hidden_signal_entwicklungspotenzial_variants_unify():
    a = extract_features("Grosses Entwicklungspotenzial.", REAL_KEYWORDS)
    b = extract_features("Grosses Entwicklungspotential.", REAL_KEYWORDS)
    assert "entwicklungspotenzial" in a.hidden_signals
    assert "entwicklungspotenzial" in b.hidden_signals


def test_zwangsversteigerung_detected_even_though_not_in_hidden_phrases_list():
    features = extract_features("Objekt aus einer Zwangsversteigerung.", REAL_KEYWORDS)
    assert "zwangsversteigerung" in features.hidden_signals
    assert features.is_foreclosure is True


def test_is_monument_denkmal_variants():
    assert extract_features("Das Haus ist ein Denkmal.", REAL_KEYWORDS).is_monument is True
    assert (
        extract_features("Denkmalgeschütztes Bauernhaus.", REAL_KEYWORDS).is_monument is True
    )
    assert extract_features("Ein Baudenkmal von 1850.", REAL_KEYWORDS).is_monument is True
    assert extract_features("Ganz normales Haus.", REAL_KEYWORDS).is_monument is False


def test_is_foreclosure_zvg_abbreviation():
    assert extract_features("ZVG-Objekt, siehe Amtsgericht.", REAL_KEYWORDS).is_foreclosure is True
    assert extract_features("Normaler Verkauf.", REAL_KEYWORDS).is_foreclosure is False


def test_is_private_seller():
    assert extract_features("Verkauf von privat.", REAL_KEYWORDS).is_private_seller is True
    assert (
        extract_features("Verkauf durch Makler XY.", REAL_KEYWORDS).is_private_seller is False
    )


def test_is_off_market_signal():
    chiffre = extract_features("Chiffre-Anzeige, Details vertraulich.", REAL_KEYWORDS)
    assert chiffre.is_off_market_signal is True
    normal = extract_features("Ganz normales Inserat.", REAL_KEYWORDS)
    assert normal.is_off_market_signal is False


def test_empty_text_returns_empty_extraction():
    features = extract_features("", REAL_KEYWORDS)
    assert features.outbuildings == []
    assert features.hidden_signals == []
    assert features.exclusion_flags == []
    assert features.is_foreclosure is False
    assert features.is_monument is False


# --------------------------------------------------------------------------- #
# classify_property_type
# --------------------------------------------------------------------------- #


def test_classify_prefers_more_specific_term():
    result = classify_property_type(
        "Anwesen mit Vierseithof-Charakter", "Ein schönes Anwesen.", REAL_KEYWORDS
    )
    assert result == "vierseithof"


def test_classify_title_takes_priority_over_description():
    title = "Gepflegter Bauernhof"
    description = "In der Nähe befindet sich ein Vierseithof zum Vergleich."
    assert classify_property_type(title, description, REAL_KEYWORDS) == "bauernhof"


def test_classify_falls_back_to_description():
    title = "Schönes Grundstück in Bayern"
    description = "Ehemaliger Bauernhof mit viel Potenzial."
    assert classify_property_type(title, description, REAL_KEYWORDS) == "ehemaliger_bauernhof"


def test_classify_returns_none_when_nothing_matches():
    assert classify_property_type("Eigentumswohnung", "Zentrale Lage.", REAL_KEYWORDS) is None


def test_classify_handles_empty_strings():
    assert classify_property_type("", "", REAL_KEYWORDS) is None
