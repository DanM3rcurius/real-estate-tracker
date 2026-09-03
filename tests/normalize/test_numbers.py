"""parse_german_number / parse_price / parse_area / prices_equivalent."""

from __future__ import annotations

import pytest

from hofradar.db.enums import PriceType
from hofradar.normalize import parse_area, parse_german_number, parse_price
from hofradar.normalize.numbers import prices_equivalent

# --------------------------------------------------------------------------- #
# parse_german_number
# --------------------------------------------------------------------------- #

NUMBER_CASES = [
    pytest.param("750.000", 750000.0, id="thousands-dot"),
    pytest.param("750000", 750000.0, id="plain-digits"),
    pytest.param("750.000,00", 750000.0, id="thousands-and-cents"),
    pytest.param("0,75", 0.75, id="decimal-comma"),
    pytest.param("1,2", 1.2, id="decimal-comma-short"),
    pytest.param("1.234.567", 1234567.0, id="multi-group-thousands"),
    pytest.param("750k", 750000.0, id="k-suffix"),
    pytest.param("750 k", 750000.0, id="k-suffix-spaced"),
    pytest.param("0,75 Mio", 750000.0, id="mio-suffix"),
    pytest.param("1,2 Mio", 1200000.0, id="mio-suffix-2"),
    pytest.param("3 Tsd", 3000.0, id="tsd-suffix"),
    pytest.param("Baujahr 1995", 1995.0, id="embedded-in-sentence"),
    pytest.param(None, None, id="none"),
    pytest.param("keine Zahl hier", None, id="no-number"),
    pytest.param("", None, id="empty-string"),
]


@pytest.mark.parametrize("text,expected", NUMBER_CASES)
def test_parse_german_number(text, expected):
    result = parse_german_number(text)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_parse_german_number_trailing_sentence_dot_not_swallowed():
    # "1995." at the end of a sentence: the period must not be misread as
    # the start of a thousands group.
    assert parse_german_number("Baujahr 1995.") == pytest.approx(1995.0)


# --------------------------------------------------------------------------- #
# parse_price - the blueprint's canonical equivalence cases
# --------------------------------------------------------------------------- #


def test_price_750k_equals_750000_euro_formatted():
    value_a, type_a = parse_price("750k")
    value_b, type_b = parse_price("750.000 €")
    assert value_a == value_b == pytest.approx(750000.0)
    assert type_a == type_b == PriceType.ASKING.value


def test_price_749900_is_a_different_value_but_within_tolerance():
    value_a, _ = parse_price("750.000 €")
    value_b, _ = parse_price("749900")
    assert value_a == pytest.approx(750000.0)
    assert value_b == pytest.approx(749900.0)
    assert value_a != value_b
    assert prices_equivalent(value_a, value_b, tol_pct=1.0) is True
    assert prices_equivalent(value_a, value_b, tol_pct=0.01) is False


@pytest.mark.parametrize(
    "a,b,tol_pct,expected",
    [
        (100.0, 100.0, 1.0, True),
        (100.0, 101.0, 1.0, True),
        (100.0, 102.0, 1.0, False),
        (None, None, 1.0, True),
        (100.0, None, 1.0, False),
        (None, 100.0, 1.0, False),
        (0.0, 0.0, 1.0, True),
    ],
)
def test_prices_equivalent(a, b, tol_pct, expected):
    assert prices_equivalent(a, b, tol_pct=tol_pct) is expected


PRICE_VALUE_CASES = [
    pytest.param("750.000 €", 750000.0, PriceType.ASKING, id="dot-currency"),
    pytest.param("750000", 750000.0, PriceType.ASKING, id="plain-6-digit"),
    pytest.param("€ 750.000,00", 750000.0, PriceType.ASKING, id="leading-currency-cents"),
    pytest.param("750 T€", 750000.0, PriceType.ASKING, id="t-euro"),
    pytest.param("750k", 750000.0, PriceType.ASKING, id="k-suffix"),
    pytest.param("0,75 Mio", 750000.0, PriceType.ASKING, id="mio-bare"),
    pytest.param("1,2 Mio €", 1200000.0, PriceType.ASKING, id="mio-with-currency"),
]


@pytest.mark.parametrize("text,expected_value,expected_type", PRICE_VALUE_CASES)
def test_parse_price_values(text, expected_value, expected_type):
    value, price_type = parse_price(text)
    assert value == pytest.approx(expected_value)
    assert price_type == expected_type.value


PRICE_TYPE_CASES = [
    pytest.param("VB", None, PriceType.NEGOTIABLE, id="vb-bare"),
    pytest.param("750.000 € VB", 750000.0, PriceType.NEGOTIABLE, id="price-plus-vb"),
    pytest.param("Verhandlungsbasis", None, PriceType.NEGOTIABLE, id="verhandlungsbasis"),
    pytest.param("VHB", None, PriceType.NEGOTIABLE, id="vhb"),
    pytest.param("Preis auf Anfrage", None, PriceType.ON_REQUEST, id="preis-auf-anfrage"),
    pytest.param("auf Anfrage", None, PriceType.ON_REQUEST, id="auf-anfrage"),
    pytest.param(
        "Verkehrswert 420.000 €", 420000.0, PriceType.AUCTION_MIN, id="verkehrswert"
    ),
    pytest.param("Mindestgebot 300.000 EUR", 300000.0, PriceType.AUCTION_MIN, id="mindestgebot"),
    pytest.param("650.000 € Festpreis", 650000.0, PriceType.ASKING, id="festpreis-is-asking"),
]


@pytest.mark.parametrize("text,expected_value,expected_type", PRICE_TYPE_CASES)
def test_parse_price_types(text, expected_value, expected_type):
    value, price_type = parse_price(text)
    if expected_value is None:
        assert value is None
    else:
        assert value == pytest.approx(expected_value)
    assert price_type == expected_type.value


def test_parse_price_none_and_empty():
    assert parse_price(None) == (None, PriceType.UNKNOWN.value)
    assert parse_price("") == (None, PriceType.UNKNOWN.value)
    assert parse_price("   ") == (None, PriceType.UNKNOWN.value)


def test_parse_price_ambiguity_guard_bare_number():
    # A bare "750" has no currency marker, no magnitude marker, and fewer
    # than 4 digits - it must NOT be treated as a price.
    value, price_type = parse_price("750")
    assert value is None
    assert price_type == PriceType.UNKNOWN.value


def test_parse_price_ambiguity_guard_four_digits_is_accepted():
    value, price_type = parse_price("7500")
    assert value == pytest.approx(7500.0)
    assert price_type == PriceType.ASKING.value


def test_parse_price_ambiguity_guard_magnitude_overrides_short_digit_count():
    value, _ = parse_price("750k")
    assert value == pytest.approx(750000.0)


# --------------------------------------------------------------------------- #
# parse_area - the blueprint's canonical equivalence case
# --------------------------------------------------------------------------- #


def test_area_5000_sqm_ha_all_equal():
    a = parse_area("5.000 m²")
    b = parse_area("5000 qm")
    c = parse_area("0,5 ha")
    assert a == b == c == pytest.approx(5000.0)


AREA_CASES = [
    pytest.param("5.000 m²", 5000.0, id="m2-superscript-dot-thousands"),
    pytest.param("5000 qm", 5000.0, id="qm-plain"),
    pytest.param("5000 m2", 5000.0, id="m2-ascii"),
    pytest.param("0,5 ha", 5000.0, id="ha-comma-decimal"),
    pytest.param("1 Hektar", 10000.0, id="hektar-spelled-out"),
    pytest.param("5.000 Quadratmeter", 5000.0, id="quadratmeter-spelled-out"),
    pytest.param("ca. 2.700 m²", 2700.0, id="ca-prefix-ignored"),
    pytest.param("1 Tagwerk", 3407.0, id="tagwerk-bavarian"),
    pytest.param("2 Tagwerk", 6814.0, id="tagwerk-multiple"),
    pytest.param(None, None, id="none"),
    pytest.param("", None, id="empty"),
    pytest.param("kein Grundstück angegeben", None, id="no-number"),
]


@pytest.mark.parametrize("text,expected", AREA_CASES)
def test_parse_area(text, expected):
    result = parse_area(text)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)
        assert isinstance(result, float)
