"""The reader's own Sanierungsstufe beats every inference rule.

Somebody who stood in the building knows more than a tag, a condition word or
the age of the substance. So ``user_renovation_tier`` wins outright, counts as
evidence a person stood behind (``renovation_evidence == "manual"``), and may
therefore hard-reject on total cost like an observed tier. What the rules
would have said stays computable (``automatic_renovation_tier``), because the
dossier prints it beside the override.
"""

from __future__ import annotations

from factories import make_profile, make_property

from hofradar.costmodel import (
    EVIDENCE_MANUAL,
    automatic_renovation_tier,
    estimate_costs,
    infer_renovation_tier,
    renovation_evidence,
)
from hofradar.db.enums import RenovationTier
from hofradar.scoring.engine import FLAG_COST_INFERRED, REJECT_TOTAL_COST, score_property


def test_manual_beats_the_pre_modern_age_bump() -> None:
    control = make_property(year_built=1890, condition="gut")
    assert infer_renovation_tier(control) is RenovationTier.MEDIUM, "the bump the test overrides"

    prop = make_property(year_built=1890, condition="gut", user_renovation_tier="light")
    assert infer_renovation_tier(prop) is RenovationTier.LIGHT


def test_manual_beats_the_age_fallback() -> None:
    prop = make_property(year_built=1890, condition=None, user_renovation_tier="medium")
    assert infer_renovation_tier(prop) is RenovationTier.MEDIUM


def test_manual_beats_an_abrissreif_tag() -> None:
    control = make_property(building_features=["abrissreif"], year_built=1890)
    assert infer_renovation_tier(control) is RenovationTier.COMPLETE

    prop = make_property(
        building_features=["abrissreif"], year_built=1890, user_renovation_tier="heavy"
    )
    assert infer_renovation_tier(prop) is RenovationTier.HEAVY


def test_the_cost_model_uses_the_manual_tier() -> None:
    profile = make_profile()
    auto = estimate_costs(make_property(year_built=1890), profile)
    manual = estimate_costs(make_property(year_built=1890, user_renovation_tier="light"), profile)

    assert auto.renovation_tier == "heavy"
    assert manual.renovation_tier == "light"
    assert manual.renovation_mid < auto.renovation_mid
    assert manual.renovation_evidence == EVIDENCE_MANUAL


def test_renovation_evidence_says_manual() -> None:
    prop = make_property(condition="sanierungsbeduerftig", user_renovation_tier="light")
    assert renovation_evidence(prop) == "manual"


def test_a_stored_value_outside_the_manual_tiers_is_ignored() -> None:
    """``unknown`` or a typo must never silently become a verdict."""
    for raw in ("unknown", "leicht", ""):
        prop = make_property(year_built=1890, user_renovation_tier=raw)
        assert infer_renovation_tier(prop) is RenovationTier.HEAVY
        assert renovation_evidence(prop) == "inferred"


def test_automatic_tier_ignores_the_override() -> None:
    prop = make_property(
        building_features=["abrissreif"], year_built=1890, user_renovation_tier="light"
    )
    assert automatic_renovation_tier(prop) is RenovationTier.COMPLETE
    # Pure: asking did not clear the reader's value.
    assert prop.user_renovation_tier == "light"


def test_automatic_tier_follows_the_profile_thresholds() -> None:
    prop = make_property(year_built=1970, condition=None, user_renovation_tier="light")
    profile = make_profile()
    assert automatic_renovation_tier(prop, profile.renovation) is RenovationTier.MEDIUM
    stricter = profile.model_copy(
        update={"renovation": profile.renovation.model_copy(update={"pre_modern_year": 1980})}
    )
    assert automatic_renovation_tier(prop, stricter.renovation) is RenovationTier.HEAVY


def test_the_cost_gate_rejects_on_a_manual_tier() -> None:
    """Same property as the inferred-cost test, which only flags. A tier the
    reader set is stood-behind, so an over-budget total rejects."""
    profile = make_profile(total_budget_hard_max=800_000)
    prop = make_property(
        price=350_000, living_sqm=400, year_built=1890, user_renovation_tier="complete"
    )

    result = score_property(prop, profile)

    assert REJECT_TOTAL_COST in result.reject_reasons
    assert FLAG_COST_INFERRED not in result.flags


def test_the_cost_assumptions_say_a_tier_was_set_by_hand() -> None:
    manual = estimate_costs(
        make_property(year_built=1890, user_renovation_tier="light"), make_profile()
    )
    assert "Sanierungsstufe leicht (manuell gesetzt)" in manual.assumptions[0]

    inferred = estimate_costs(make_property(year_built=1890), make_profile())
    assert "manuell" not in inferred.assumptions[0]
