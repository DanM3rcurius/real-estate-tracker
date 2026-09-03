"""The profile's ``exclude`` vocabulary has to be a gate, not a decoration.

An end-to-end smoke run caught this: a Neubau Reihenmittelhaus was parsed
correctly, its exclusion terms were extracted correctly, and it still ranked
third, because nothing downstream ever read ``exclusion_flags``. These tests
pin the gate and its one deliberate escape hatch.
"""

from __future__ import annotations

from hofradar.config import SearchProfile
from hofradar.costmodel import estimate_costs
from hofradar.db.models import Property
from hofradar.scoring import score_property
from hofradar.scoring.engine import (
    FLAG_EXCLUSION_OVERRIDDEN,
    REJECT_EXCLUDED_TYPE,
)


def _property(**overrides) -> Property:
    defaults = dict(
        public_id="hof-test",
        canonical_title="Testobjekt",
        town="Rosenheim",
        price=640_000.0,
        land_sqm=210.0,
        living_sqm=140.0,
        year_built=2024,
        distance_air_km=22.3,
        distance_driving_km=28.0,
        property_type=None,
        building_features=[],
        outbuildings=[],
        special_features=[],
        exclusion_flags=[],
        evidence={},
    )
    defaults.update(overrides)
    return Property(**defaults)


def test_an_excluded_type_is_rejected_outright() -> None:
    profile = SearchProfile()
    prop = _property(exclusion_flags=["neubau", "reihenhaus", "reihenmittelhaus"])
    result = score_property(prop, profile, cost=estimate_costs(prop, profile))

    assert result.rejected is True
    assert REJECT_EXCLUDED_TYPE in result.reject_reasons


def test_a_farm_with_outbuildings_survives_its_exclusion_terms() -> None:
    """"Neubau eines Stadels geplant" on a real Hofstelle is not a Neubau."""
    profile = SearchProfile()
    prop = _property(
        property_type="vierseithof",
        exclusion_flags=["neubau"],
        outbuildings=["scheune", "tenne"],
        price=560_000.0,
        land_sqm=6_000.0,
        living_sqm=260.0,
        year_built=1889,
    )
    result = score_property(prop, profile, cost=estimate_costs(prop, profile))

    assert REJECT_EXCLUDED_TYPE not in result.reject_reasons
    assert FLAG_EXCLUSION_OVERRIDDEN in result.flags


def test_an_excluded_type_cannot_vouch_for_itself() -> None:
    """A property_type that is itself an exclusion is not counter-evidence."""
    profile = SearchProfile()
    prop = _property(property_type="reihenhaus", exclusion_flags=["reihenhaus"])
    result = score_property(prop, profile, cost=estimate_costs(prop, profile))

    assert REJECT_EXCLUDED_TYPE in result.reject_reasons


def test_the_gate_can_be_switched_off() -> None:
    profile = SearchProfile()
    profile.gates.reject_excluded = False
    prop = _property(exclusion_flags=["neubau", "reihenhaus"])
    result = score_property(prop, profile, cost=estimate_costs(prop, profile))

    assert REJECT_EXCLUDED_TYPE not in result.reject_reasons
