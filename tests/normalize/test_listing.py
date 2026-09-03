"""normalize_listing: full RawListing -> NormalizedListing round trip."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from hofradar.config import load_keywords
from hofradar.contracts import RawListing
from hofradar.db.enums import PriceType
from hofradar.normalize import normalize_listing

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "fixtures"))
from listings_normalize import (  # noqa: E402
    FORECLOSURE_DENKMAL,
    SPARSE_UNPARSEABLE,
    VIERSEITHOF_ROSENHEIM,
)

KEYWORDS = load_keywords()


def test_full_listing_round_trip():
    result = normalize_listing(VIERSEITHOF_ROSENHEIM, KEYWORDS)

    assert result.source_key == "test_source"
    assert result.url == VIERSEITHOF_ROSENHEIM.url

    assert result.price == pytest.approx(750000.0)
    assert result.price_type == PriceType.ASKING.value

    assert result.land_sqm == pytest.approx(5000.0)
    assert result.living_sqm == pytest.approx(180.0)
    assert result.usable_sqm == pytest.approx(220.0)
    assert result.rooms == pytest.approx(7.0)
    assert result.year_built == 1890

    assert result.street == "Musterstraße 12"
    assert result.postcode == "83024"
    assert result.town == "Rosenheim"

    assert result.property_type == "vierseithof"
    assert "scheune" in result.outbuildings
    assert "stadel" in result.outbuildings or "stadl" in result.outbuildings
    assert "privatverkauf" in result.hidden_signals
    assert "kein_makler" in result.hidden_signals
    assert result.is_private_seller is True

    assert result.text_hash is not None
    assert len(result.text_hash) > 0

    # Load-bearing fields must carry evidence quoting the raw source string.
    for field_name, raw in (
        ("price", VIERSEITHOF_ROSENHEIM.price_raw),
        ("land_sqm", VIERSEITHOF_ROSENHEIM.land_raw),
        ("living_sqm", VIERSEITHOF_ROSENHEIM.living_raw),
        ("year_built", VIERSEITHOF_ROSENHEIM.year_raw),
        ("town", VIERSEITHOF_ROSENHEIM.location_raw),
    ):
        assert field_name in result.evidence, f"missing evidence for {field_name}"
        ev = result.evidence[field_name]
        assert ev["quote"] == raw
        assert ev["source"] == "test_source"
        assert ev["url"] == VIERSEITHOF_ROSENHEIM.url

    assert result.source_date is not None


def test_sparse_listing_produces_warnings_not_crashes():
    result = normalize_listing(SPARSE_UNPARSEABLE, KEYWORDS)

    # Ambiguity guard: bare "750" is not a price.
    assert result.price is None
    assert result.price_type == PriceType.UNKNOWN.value
    assert "price" not in result.evidence
    assert any("price" in w for w in result.warnings)

    # Unparseable year raw string.
    assert result.year_built is None
    assert any("year_built" in w for w in result.warnings)

    assert result.land_sqm is None
    assert result.town is None


def test_foreclosure_and_monument_listing():
    result = normalize_listing(FORECLOSURE_DENKMAL, KEYWORDS)

    assert result.price == pytest.approx(420000.0)
    assert result.price_type == PriceType.AUCTION_MIN.value
    assert result.is_foreclosure is True
    assert result.is_monument is True

    assert result.land_sqm == pytest.approx(10000.0)
    assert result.living_sqm == pytest.approx(150.0)
    assert result.rooms == pytest.approx(5.5)
    assert result.year_built == 1920

    assert result.town == "Vogtareuth"
    assert result.district == "Rosenheim"

    assert result.source_date is not None
    assert result.source_date.year == 2026
    assert result.source_date.month == 3
    assert result.source_date.day == 12


def test_normalize_listing_preserves_passthrough_fields():
    result = normalize_listing(VIERSEITHOF_ROSENHEIM, KEYWORDS)
    assert result.external_id == VIERSEITHOF_ROSENHEIM.external_id
    assert result.title == VIERSEITHOF_ROSENHEIM.title
    assert result.description == VIERSEITHOF_ROSENHEIM.description


def test_exclusion_flags_generate_a_warning():
    raw = RawListing(
        source_key="test_source",
        url="https://example.test/listings/excluded",
        title="Moderne Eigentumswohnung",
        description="Neubau, Erstbezug.",
        price_raw="350.000 €",
    )
    result = normalize_listing(raw, KEYWORDS)
    assert "eigentumswohnung" in result.exclusion_flags
    assert any("exclusion" in w.lower() for w in result.warnings)
