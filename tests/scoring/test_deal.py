"""deal_score and the Capital Risk Gate, including the blueprint's case 7."""

from __future__ import annotations

import pytest
from factories import attach_source, make_property, make_source

from hofradar.config import SearchProfile
from hofradar.costmodel import estimate_costs
from hofradar.db.enums import CapitalRisk
from hofradar.scoring import deal_score, ranked_properties, rescore_all, score_property
from hofradar.scoring.deal import SANIERUNGSRISIKO_FLAG
from hofradar.scoring.engine import (
    REJECT_EXCEPTIONAL_WITHOUT_DEVELOPMENT,
    REJECT_TOTAL_COST,
)


@pytest.fixture()
def profile() -> SearchProfile:
    return SearchProfile()


def test_a_comfortable_deal_is_low_risk(profile: SearchProfile) -> None:
    prop = make_property(price=380_000, living_sqm=160, land_sqm=6_000, year_built=1975,
                         building_features=["modernisiert"], outbuildings=["Scheune"])
    score, breakdown = deal_score(prop, profile, estimate_costs(prop, profile))
    assert breakdown["capital_risk"] == CapitalRisk.LOW
    assert SANIERUNGSRISIKO_FLAG not in breakdown["flags"]
    assert score > 70


def test_over_the_budget_slider_is_high_risk(profile: SearchProfile) -> None:
    """Over ``total_budget_max`` but still under the hard ceiling."""
    prop = make_property(price=650_000, living_sqm=200, land_sqm=6_000, year_built=1975,
                         building_features=["renovierungsbeduerftig"],
                         outbuildings=["Scheune", "Stall"])
    cost = estimate_costs(prop, profile)
    assert profile.budget.total_budget_max < cost.total_mid
    assert cost.total_mid <= profile.budget.effective_total_hard_max
    _, breakdown = deal_score(prop, profile, cost)
    assert breakdown["capital_risk"] == CapitalRisk.HIGH
    assert "OVER_BUDGET" in breakdown["flags"]


class TestBlueprintCase7:
    """Purchase 700k with a renovation estimate around 1.0M.

    Expected: capital_risk EXTREME, a SANIERUNGSRISIKO flag, and no place in
    the top ten under the default 1.2M budget.
    """

    @pytest.fixture()
    def money_pit(self, session):
        source = make_source(session, key="portal")
        prop = make_property(
            session,
            canonical_title="Vierseithof, kernsanierungsbeduerftig",
            price=700_000,
            living_sqm=220,
            land_sqm=9_000,
            year_built=1890,
            building_features=["kernsanierung"],
            outbuildings=["Scheune", "Stall", "Tenne"],
        )
        attach_source(session, prop, source)
        return prop

    def test_capital_risk_and_flag(self, money_pit, profile: SearchProfile) -> None:
        cost = estimate_costs(money_pit, profile)
        assert cost.renovation_mid == pytest.approx(1_000_000, rel=0.15)
        assert cost.total_mid > profile.budget.effective_total_hard_max

        score, breakdown = deal_score(money_pit, profile, cost)
        assert breakdown["capital_risk"] == CapitalRisk.EXTREME
        assert SANIERUNGSRISIKO_FLAG in breakdown["flags"]
        assert score <= 10.0

    def test_it_is_rejected_outright(self, money_pit, profile: SearchProfile) -> None:
        result = score_property(money_pit, profile)
        assert result.capital_risk == CapitalRisk.EXTREME
        assert SANIERUNGSRISIKO_FLAG in result.flags
        assert result.rejected is True
        assert REJECT_TOTAL_COST in result.reject_reasons

    def test_it_never_reaches_the_top_ten(self, session, money_pit, profile: SearchProfile) -> None:
        source = make_source(session, key="portal2")
        for index in range(12):
            good = make_property(
                session,
                canonical_title=f"Sacherl in Alleinlage {index}",
                price=380_000 + index * 1_000,
                living_sqm=150,
                land_sqm=6_000,
                year_built=1975,
                building_features=["modernisiert"],
            )
            attach_source(session, good, source)
        rescore_all(session, profile)

        top_ten = ranked_properties(session, profile, limit=10)
        assert len(top_ten) == 10
        assert money_pit.id not in {prop.id for prop, _ in top_ten}

        everything = ranked_properties(session, profile, include_rejected=True)
        rejected = {prop.id: score for prop, score in everything if score.rejected}
        assert money_pit.id in rejected
        assert rejected[money_pit.id].capital_risk == CapitalRisk.EXTREME


class TestExceptionalBudgetCarveOut:
    """Between ``effective_total_exceptional_max`` and ``effective_total_hard_max``
    only provable development potential keeps a property alive."""

    def _prop(self, session, description: str):
        source = make_source(session, key=f"src-{description[:6]}")
        prop = make_property(
            session,
            canonical_title="Hofstelle mit Nebengebaeuden",
            description=description,
            price=680_000,
            living_sqm=180,
            land_sqm=9_000,
            year_built=1900,
            building_features=["sanierungsbeduerftig"],
            outbuildings=["Scheune"],
        )
        attach_source(session, prop, source)
        return prop

    def test_inside_the_band_without_development_is_rejected(
        self, session, profile: SearchProfile
    ) -> None:
        prop = self._prop(session, "Ruhige Lage am Ortsrand.")
        cost = estimate_costs(prop, profile)
        assert (
            profile.budget.effective_total_exceptional_max
            < cost.total_mid
            <= profile.budget.effective_total_hard_max
        )
        result = score_property(prop, profile, cost=cost)
        assert REJECT_EXCEPTIONAL_WITHOUT_DEVELOPMENT in result.reject_reasons

    def test_inside_the_band_with_a_granted_division_survives(
        self, session, profile: SearchProfile
    ) -> None:
        prop = self._prop(
            session, "Die Teilungsgenehmigung liegt vor, zwei Bauplaetze am Ortsrand."
        )
        cost = estimate_costs(prop, profile)
        result = score_property(prop, profile, cost=cost)
        assert result.breakdown["fit"]["development_score"] >= (
            profile.gates.exceptional_development_min
        )
        assert REJECT_EXCEPTIONAL_WITHOUT_DEVELOPMENT not in result.reject_reasons
        assert "EXCEPTIONAL_DEVELOPMENT_CARVE_OUT" in result.flags


def test_the_price_per_sqm_references_move_with_the_budget_slider() -> None:
    generous = SearchProfile.model_validate({"budget": {"total_budget_max": 1_200_000}})
    tight = SearchProfile.model_validate({"budget": {"total_budget_max": 800_000}})
    prop = make_property(price=500_000, land_sqm=5_000, living_sqm=200)

    generous_bd = deal_score(prop, generous, estimate_costs(prop, generous))[1]
    tight_bd = deal_score(prop, tight, estimate_costs(prop, tight))[1]
    assert generous_bd["land_price_reference_eur_per_sqm"] == 150.0
    assert tight_bd["land_price_reference_eur_per_sqm"] == 100.0
    assert tight_bd["land_price_score"] < generous_bd["land_price_score"]
