"""The reader's Sanierungsstufe wins outright over every inferred signal.

``Property.user_renovation_tier`` is set from the dossier, not by a crawl, and
it replaces the listing's inference entirely: no age bump, no worst-signal
rule from tags or condition. ``listing_renovation_tier`` must keep answering
what the advert alone implies, so the dossier can show both figures and a
reset can say what it returns to. See docs/DECISIONS.md entry 30.
"""

from __future__ import annotations

import pytest
from factories import make_property

from hofradar.config import SearchProfile
from hofradar.costmodel import (
    estimate_costs,
    infer_renovation_tier,
    listing_renovation_evidence,
    listing_renovation_tier,
    reader_renovation_tier,
    renovation_evidence,
)
from hofradar.db.enums import RenovationTier


@pytest.fixture()
def profile() -> SearchProfile:
    return SearchProfile()


def test_reader_tier_wins_outright_over_a_bump_the_listing_would_earn() -> None:
    """A 1890 build tagged "renoviert" would be bumped from LIGHT to MEDIUM by
    the age rule (renovation.py's pre-1960 bump); the reader saying "light"
    must not be re-bumped - their judgement replaces the inference, it does
    not feed it."""
    prop = make_property(
        building_features=["renoviert"], year_built=1890, user_renovation_tier="light"
    )

    # The listing alone would have been bumped to medium.
    assert listing_renovation_tier(prop) is RenovationTier.MEDIUM
    # The reader's own tier is what is priced.
    assert infer_renovation_tier(prop) is RenovationTier.LIGHT


def test_reader_tier_overrides_an_unrelated_listing_inference() -> None:
    prop = make_property(
        building_features=["abrissreif"], year_built=1890, user_renovation_tier="light"
    )

    assert listing_renovation_tier(prop) is RenovationTier.COMPLETE
    assert infer_renovation_tier(prop) is RenovationTier.LIGHT


def test_listing_renovation_tier_ignores_the_reader() -> None:
    prop = make_property(condition=None, year_built=1890, user_renovation_tier="light")

    assert listing_renovation_tier(prop) is RenovationTier.HEAVY


def test_renovation_evidence_is_reader_when_set() -> None:
    prop = make_property(condition=None, year_built=1890, user_renovation_tier="medium")

    assert renovation_evidence(prop) == "reader"
    # The listing's own evidence classification is untouched.
    assert listing_renovation_evidence(prop) == "inferred"


def test_renovation_evidence_without_a_reader_tier_is_the_listings() -> None:
    prop = make_property(condition="sanierungsbeduerftig", year_built=1890)

    assert renovation_evidence(prop) == "observed"


@pytest.mark.parametrize("bogus", ["unknown", "bogus", ""])
def test_invalid_stored_values_are_ignored(bogus: str) -> None:
    prop = make_property(condition=None, year_built=1890, user_renovation_tier=bogus)

    assert reader_renovation_tier(prop) is None
    assert renovation_evidence(prop) == "inferred"
    # Falls through to the listing's own (pessimistic age-fallback) tier.
    assert infer_renovation_tier(prop) is RenovationTier.HEAVY


class TestEstimateCostsWithAReaderTier:
    def test_estimate_costs_uses_the_reader_tiers_rates(self, profile: SearchProfile) -> None:
        prop = make_property(
            condition=None, year_built=1890, living_sqm=150, user_renovation_tier="light"
        )
        cost = estimate_costs(prop, profile)

        assert cost.renovation_tier == "light"
        light_low = profile.renovation.light_min
        light_high = profile.renovation.light_max
        assert cost.breakdown["rate_per_sqm_low"] == pytest.approx(light_low)
        assert cost.breakdown["rate_per_sqm_high"] == pytest.approx(light_high)

    def test_first_assumption_names_selbst_gesetzt_and_the_listing_word(
        self, profile: SearchProfile
    ) -> None:
        prop = make_property(condition=None, year_built=1890, user_renovation_tier="medium")
        cost = estimate_costs(prop, profile)

        first = cost.assumptions[0]
        assert "selbst gesetzt" in first
        assert "mittel" in first
        # The listing alone (pre-1960, nothing stated) would have been schwer.
        assert "schwer" in first

    def test_without_an_override_the_sentence_is_unchanged(self, profile: SearchProfile) -> None:
        prop = make_property(condition=None, year_built=1890)
        cost = estimate_costs(prop, profile)

        first = cost.assumptions[0]
        assert "selbst gesetzt" not in first
        assert first.startswith("Sanierungsstufe schwer mit")
