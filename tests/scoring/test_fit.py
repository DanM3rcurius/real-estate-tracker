"""The five judgement components of fit_score (geography and price have their
own file, ``test_blueprint_bands.py``)."""

from __future__ import annotations

from typing import Any

import pytest
from factories import make_property

from hofradar.config import SearchProfile
from hofradar.scoring import fit_score


@pytest.fixture()
def profile() -> SearchProfile:
    return SearchProfile()


def bare(**kwargs: Any):
    """A property that says nothing, so one signal can be tested at a time."""
    defaults: dict[str, Any] = {
        "canonical_title": "Anwesen",
        "description": "Objekt in Oberbayern.",
        "property_type": None,
        "building_features": [],
        "outbuildings": [],
        "special_features": [],
        "year_built": 1980,
        "is_monument": False,
    }
    defaults.update(kwargs)
    return make_property(**defaults)


class TestLand:
    @pytest.mark.parametrize(
        ("land_sqm", "expected"),
        [(500, 0.0), (999, 0.0), (1_000, 5.0), (1_999, 5.0), (2_000, 10.0),
         (3_999, 10.0), (4_000, 13.0), (7_999, 13.0), (8_000, 15.0), (30_000, 15.0)],
    )
    def test_bands_are_multiples_of_preferred_min_sqm(
        self, profile: SearchProfile, land_sqm: float, expected: float
    ) -> None:
        assert fit_score(bare(land_sqm=land_sqm), profile)[1]["land_score"] == expected

    def test_the_bands_move_with_the_land_slider(self) -> None:
        profile = SearchProfile.model_validate({"land": {"preferred_min_sqm": 4_000}})
        assert fit_score(bare(land_sqm=2_000), profile)[1]["land_score"] == 5.0
        assert fit_score(bare(land_sqm=8_000), profile)[1]["land_score"] == 13.0

    def test_unknown_plot_size_scores_nothing_and_says_why(
        self, profile: SearchProfile
    ) -> None:
        breakdown = fit_score(bare(land_sqm=None), profile)[1]
        assert breakdown["land_score"] == 0.0
        assert "unknown" in breakdown["land_note"]


class TestSubstance:
    def test_full_farmstead_is_capped_at_twenty(self, profile: SearchProfile) -> None:
        prop = bare(
            canonical_title="Historische Hofstelle mit Stadl, Stall und Tenne",
            year_built=1860,
        )
        assert fit_score(prop, profile)[1]["substance_score"] == 20.0

    def test_components_add_up(self, profile: SearchProfile) -> None:
        sacherl = bare(canonical_title="Sacherl bei Rosenheim")
        assert fit_score(sacherl, profile)[1]["substance_score"] == 10.0
        with_barn = bare(canonical_title="Sacherl mit Scheune")
        assert fit_score(with_barn, profile)[1]["substance_score"] == 13.0
        with_stable = bare(canonical_title="Sacherl mit Scheune und Stall")
        assert fit_score(with_stable, profile)[1]["substance_score"] == 15.0

    def test_a_plain_house_scores_nothing(self, profile: SearchProfile) -> None:
        assert fit_score(bare(), profile)[1]["substance_score"] == 0.0

    def test_a_listed_building_counts_as_historic(self, profile: SearchProfile) -> None:
        prop = bare(canonical_title="Hofstelle", is_monument=True)
        assert fit_score(prop, profile)[1]["substance_score"] == 15.0


class TestSeclusion:
    @pytest.mark.parametrize(
        ("text", "expected", "label"),
        [
            ("Hof in absoluter Alleinlage", 10.0, "alleinlage"),
            ("Anwesen am Ortsrand von Vagen", 7.0, "ortsrand"),
            ("Gehoeft in einem Weiler", 4.0, "loose development"),
            ("Haus in der Ortsmitte", 1.0, "dense village (assumed - nothing stated)"),
        ],
    )
    def test_bands(
        self, profile: SearchProfile, text: str, expected: float, label: str
    ) -> None:
        breakdown = fit_score(bare(description=text), profile)[1]
        assert breakdown["seclusion_score"] == expected
        assert breakdown["seclusion_class"] == label

    def test_silence_is_scored_as_a_dense_village(self, profile: SearchProfile) -> None:
        """A farmstead that is genuinely alone is always advertised as such."""
        assert fit_score(bare(), profile)[1]["seclusion_score"] == 1.0


class TestDevelopment:
    def test_broker_wording_alone_is_four_points_not_ten(self, profile: SearchProfile) -> None:
        prop = bare(description="Grundstueck mit Entwicklungspotenzial!")
        breakdown = fit_score(prop, profile)[1]
        assert breakdown["development_score"] == 4.0
        assert "plausible only" in breakdown["development_basis"]

    def test_a_concrete_baurecht_reference_is_seven(self, profile: SearchProfile) -> None:
        prop = bare(description="Liegt im Bebauungsplan Nr. 14 der Gemeinde.")
        breakdown = fit_score(prop, profile)[1]
        assert breakdown["development_score"] == 7.0

    def test_a_granted_division_is_ten(self, profile: SearchProfile) -> None:
        prop = bare(description="Die Teilungsgenehmigung liegt vor, zwei Bauplaetze.")
        assert fit_score(prop, profile)[1]["development_score"] == 10.0

    def test_divisibility_plus_documentary_evidence_is_ten(
        self, profile: SearchProfile
    ) -> None:
        prop = bare(
            description="Das Grundstueck ist teilbar.",
            evidence={"teilbar": {"source": "amtsblatt", "url": "https://x.invalid"}},
        )
        assert fit_score(prop, profile)[1]["development_score"] == 10.0

    def test_silence_is_zero(self, profile: SearchProfile) -> None:
        breakdown = fit_score(bare(), profile)[1]
        assert breakdown["development_score"] == 0.0
        assert breakdown["development_basis"] == "unclear"


class TestOutbuildings:
    @pytest.mark.parametrize(
        ("outbuildings", "expected"),
        [
            ([], 0.0),
            (["Scheune"], 4.0),
            (["Stadel"], 4.0),
            (["Scheune", "Stall"], 7.0),
            (["Scheune", "Stall", "Tenne"], 10.0),
            (["Stadl", "Stall", "Tenne", "Remise"], 10.0),
        ],
    )
    def test_bands(
        self, profile: SearchProfile, outbuildings: list[str], expected: float
    ) -> None:
        assert fit_score(bare(outbuildings=outbuildings), profile)[1][
            "outbuildings_score"
        ] == expected


def test_components_sum_to_the_total(profile: SearchProfile) -> None:
    total, breakdown = fit_score(make_property(), profile)
    parts = (
        "geography_score",
        "price_score",
        "land_score",
        "substance_score",
        "seclusion_score",
        "development_score",
        "outbuildings_score",
    )
    assert total == pytest.approx(sum(breakdown[part] for part in parts))
    assert 0.0 <= total <= 100.0
