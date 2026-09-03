"""The cost model: pessimistic by design, and assembled from named components."""

from __future__ import annotations

import pytest
from factories import make_property

from hofradar.config import SearchProfile
from hofradar.costmodel import acquisition_costs, estimate_costs, infer_renovation_tier
from hofradar.costmodel.estimator import (
    DEFAULT_LIVING_SQM,
    LIVING_TO_FOOTPRINT_STOREYS,
    OUTBUILDING_SQM,
    ROOF_FOOTPRINT_FACTOR,
)
from hofradar.db.enums import RenovationTier


@pytest.fixture()
def profile() -> SearchProfile:
    return SearchProfile()


def test_acquisition_costs_are_the_four_bavarian_percentages(profile: SearchProfile) -> None:
    budget = profile.budget
    expected_pct = (
        budget.grunderwerbsteuer_pct
        + budget.notar_pct
        + budget.grundbuch_pct
        + budget.makler_pct
    )
    assert expected_pct == pytest.approx(0.0907)
    assert acquisition_costs(700_000, profile) == pytest.approx(700_000 * expected_pct)
    assert acquisition_costs(None, profile) == 0.0
    assert acquisition_costs(0, profile) == 0.0


def test_acquisition_costs_follow_the_config_not_a_literal() -> None:
    """A buyer without a broker sets makler_pct to zero and the model follows."""
    profile = SearchProfile.model_validate({"budget": {"makler_pct": 0.0}})
    assert acquisition_costs(700_000, profile) == pytest.approx(700_000 * 0.055)


class TestRenovationTier:
    def test_unknown_condition_on_a_pre_1960_farmstead_defaults_to_heavy(self) -> None:
        prop = make_property(condition=None, year_built=1890, building_features=[])
        assert infer_renovation_tier(prop) is RenovationTier.HEAVY

    def test_unknown_condition_and_unknown_year_is_also_heavy(self) -> None:
        prop = make_property(condition=None, year_built=None, building_features=[])
        assert infer_renovation_tier(prop) is RenovationTier.HEAVY

    def test_unknown_condition_on_a_modern_building_is_not_heavy(self) -> None:
        prop = make_property(condition=None, year_built=2005, building_features=[])
        assert infer_renovation_tier(prop) is RenovationTier.LIGHT

    @pytest.mark.parametrize(
        ("tag", "expected"),
        [
            ("abrissreif", RenovationTier.COMPLETE),
            ("entkernt", RenovationTier.COMPLETE),
            ("kernsanierung", RenovationTier.COMPLETE),
            ("sanierungsbeduerftig", RenovationTier.HEAVY),
            ("handwerkerobjekt", RenovationTier.HEAVY),
        ],
    )
    def test_heavy_tags_win(self, tag: str, expected: RenovationTier) -> None:
        prop = make_property(building_features=[tag], year_built=1890)
        assert infer_renovation_tier(prop) is expected

    @pytest.mark.parametrize("tag", ["saniert", "neuwertig", "modernisiert"])
    def test_light_tags_lighten_a_modern_building(self, tag: str) -> None:
        prop = make_property(building_features=[tag], year_built=1990)
        assert infer_renovation_tier(prop) is RenovationTier.LIGHT

    def test_unsaniert_is_never_read_as_saniert(self) -> None:
        prop = make_property(building_features=["unsaniert"], year_built=1990)
        assert infer_renovation_tier(prop) is RenovationTier.HEAVY

    def test_renovierungsbeduerftig_is_bumped_by_pre_1960_substance(self) -> None:
        old = make_property(building_features=["renovierungsbeduerftig"], year_built=1890)
        newer = make_property(building_features=["renovierungsbeduerftig"], year_built=1985)
        assert infer_renovation_tier(old) is RenovationTier.HEAVY
        assert infer_renovation_tier(newer) is RenovationTier.MEDIUM


class TestEstimateCosts:
    def test_totals_are_assembled_from_the_components(self, profile: SearchProfile) -> None:
        prop = make_property(price=600_000, living_sqm=200, outbuildings=["Scheune", "Stall"])
        cost = estimate_costs(prop, profile)

        assert cost.total_low < cost.total_mid < cost.total_high
        for band in ("low", "mid", "high"):
            assert getattr(cost, f"total_{band}") == pytest.approx(
                600_000
                + cost.acquisition_costs
                + getattr(cost, f"renovation_{band}")
                + cost.immediate_capex
            )

    def test_a_farmstead_is_not_living_sqm_times_a_rate(self, profile: SearchProfile) -> None:
        """Roof, outbuildings and utilities dominate; the house is a minority."""
        prop = make_property(price=600_000, living_sqm=200, outbuildings=["Scheune", "Stall"])
        cost = estimate_costs(prop, profile)
        b = cost.breakdown

        expected_roof_sqm = 200 / LIVING_TO_FOOTPRINT_STOREYS * ROOF_FOOTPRINT_FACTOR
        assert b["roof_sqm_used"] == pytest.approx(expected_roof_sqm, abs=0.1)
        assert b["roof"] == pytest.approx(
            expected_roof_sqm * profile.renovation.roof_per_sqm_footprint, abs=1
        )
        expected_outbuilding_sqm = OUTBUILDING_SQM["scheune"] + OUTBUILDING_SQM["stall"]
        assert b["outbuilding_sqm_used"] == pytest.approx(expected_outbuilding_sqm)
        assert b["outbuildings"] == pytest.approx(
            expected_outbuilding_sqm * profile.renovation.outbuilding_per_sqm
        )
        assert b["utilities"] == profile.renovation.utilities_base
        assert b["immediate_capex"] == profile.renovation.immediate_capex_base
        assert b["contingency"] == pytest.approx(
            profile.renovation.contingency_pct
            * (b["house"] + b["roof"] + b["outbuildings"] + b["utilities"])
        )
        assert cost.renovation_mid == pytest.approx(
            b["house"] + b["roof"] + b["outbuildings"] + b["utilities"] + b["contingency"]
        )

    def test_every_assumption_is_a_readable_sentence(self, profile: SearchProfile) -> None:
        cost = estimate_costs(make_property(), profile)
        assert len(cost.assumptions) >= 6
        for sentence in cost.assumptions:
            assert sentence.endswith(".")
            assert sentence[0].isupper()
        joined = " ".join(cost.assumptions).casefold()
        for topic in ("roof", "outbuilding", "utilities", "contingency", "acquisition"):
            assert topic in joined

    def test_unknown_living_area_falls_back_and_says_so(self, profile: SearchProfile) -> None:
        prop = make_property(living_sqm=None, usable_sqm=None)
        cost = estimate_costs(prop, profile)
        assert cost.breakdown["living_sqm_used"] == pytest.approx(DEFAULT_LIVING_SQM)
        assert any("assumed the typical" in a for a in cost.assumptions)

    def test_usable_area_is_preferred_over_the_blind_fallback(self, profile: SearchProfile) -> None:
        prop = make_property(living_sqm=None, usable_sqm=600)
        cost = estimate_costs(prop, profile)
        assert cost.breakdown["living_sqm_used"] == pytest.approx(270.0)

    def test_outbuilding_sizes_are_the_documented_constants(self, profile: SearchProfile) -> None:
        prop = make_property(outbuildings=["Scheune", "Stall", "Stadel", "Tenne", "Remise"])
        cost = estimate_costs(prop, profile)
        assert cost.breakdown["outbuilding_sqm_used"] == pytest.approx(200 + 150 + 180 + 120 + 60)

    def test_no_price_yields_a_lower_bound_and_says_so(self, profile: SearchProfile) -> None:
        cost = estimate_costs(make_property(price=None), profile)
        assert cost.purchase_price is None
        assert cost.acquisition_costs == 0.0
        assert any("lower bound" in a for a in cost.assumptions)
