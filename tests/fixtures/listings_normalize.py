"""Sample RawListing fixtures for tests/normalize.

Owned by the normalize test suite (tests/fixtures/listings_*.py). Kept
separate from the test modules so the same messy-but-realistic listing can
be reused across several tests without duplicating it.
"""

from __future__ import annotations

from hofradar.contracts import RawListing

#: A fairly clean, complete listing: full street address, concrete price,
#: land + living area, plausible year, a Vierseithof in the title, some
#: outbuildings and one hidden-marketing signal in the description.
VIERSEITHOF_ROSENHEIM = RawListing(
    source_key="test_source",
    url="https://example.test/listings/vierseithof-rosenheim",
    title="Historischer Vierseithof mit Scheune und Stadel bei Rosenheim",
    description=(
        "Grosszuegiges Anwesen mit Wirtschaftsgebaeude, Stall und Tenne. "
        "Grosses Grundstueck mit Entwicklungspotenzial. Von privat, kein Makler. "
        "Renovierungsbeduerftig, aber viel Charme."
    ),
    price_raw="750.000 €",
    land_raw="5.000 m²",
    living_raw="180 qm",
    usable_raw="220 qm",
    rooms_raw="7",
    year_raw="1890",
    location_raw="Musterstraße 12, 83024 Rosenheim",
    postcode=None,
    town=None,
    external_id="ext-001",
    source_date_raw="vor 3 Tagen",
)

#: A minimal, messy listing exercising the ambiguity guard (bare "750" is
#: not a price), a missing land unit, and no recognisable date.
SPARSE_UNPARSEABLE = RawListing(
    source_key="test_source",
    url="https://example.test/listings/sparse",
    title="Grundstück in Bayern",
    description="Weitere Informationen auf Anfrage.",
    price_raw="750",
    land_raw=None,
    living_raw=None,
    usable_raw=None,
    rooms_raw=None,
    year_raw="not a year",
    location_raw=None,
    postcode=None,
    town=None,
    external_id="ext-002",
    source_date_raw="irgendwann",
)

#: A foreclosure (ZVG) listing with an auction-minimum price and a Denkmal
#: (listed monument) flag - both booleans should fire independently.
FORECLOSURE_DENKMAL = RawListing(
    source_key="test_source",
    url="https://example.test/listings/zvg-denkmal",
    title="Zwangsversteigerung: denkmalgeschütztes Bauernhaus",
    description="Verkehrswert 420.000 EUR. Mindestgebot laut Gutachten.",
    price_raw="Verkehrswert 420.000 EUR",
    land_raw="1 Hektar",
    living_raw="150 m2",
    usable_raw=None,
    rooms_raw="5,5",
    year_raw="1920",
    location_raw="Vogtareuth (Landkreis Rosenheim)",
    postcode=None,
    town=None,
    external_id="ext-003",
    source_date_raw="12.03.2026",
)
