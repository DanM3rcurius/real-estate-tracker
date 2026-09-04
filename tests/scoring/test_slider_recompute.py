"""The central design constraint: two sliders, and everything recomputes.

The user moves *maximum distance* and *total budget*. Nothing about the
properties changes. Every score must change, must change in the right
direction, and must land in its own cache keyed by ``profile_hash`` without
disturbing the scores computed under any other profile.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from factories import attach_source, make_property, make_source
from sqlalchemy import func, select

from hofradar.config import SearchProfile
from hofradar.db.enums import ListingStatus, VerificationStatus
from hofradar.db.models import CostEstimate, Score
from hofradar.scoring import ranked_properties, rescore_all, score_property
from hofradar.scoring.engine import REJECT_AIR_DISTANCE, REJECT_TOTAL_COST

NARROW_RADIUS_KM = 40
TIGHT_BUDGET = 800_000


@pytest.fixture()
def wide() -> SearchProfile:
    """The shipped defaults: 80 km, 1.2M."""
    return SearchProfile()


@pytest.fixture()
def narrow() -> SearchProfile:
    """Distance slider dragged from 80 km down to 40 km."""
    return SearchProfile.model_validate({"radius": {"air_km_max": NARROW_RADIUS_KM}})


@pytest.fixture()
def poorer() -> SearchProfile:
    """Budget slider dragged from 1.2M down to 800k."""
    return SearchProfile.model_validate({"budget": {"total_budget_max": TIGHT_BUDGET}})


@pytest.fixture()
def estate(session, now: datetime) -> dict[str, object]:
    """Three properties that differ only in the ways the sliders care about."""
    source = make_source(session, key="portal")
    built: dict[str, object] = {}
    specs = {
        "near": {"distance_air_km": 25.0, "price": 420_000.0},
        "far": {"distance_air_km": 60.0, "price": 420_000.0},
        "pricey": {"distance_air_km": 25.0, "price": 620_000.0},
    }
    for name, spec in specs.items():
        prop = make_property(
            session,
            canonical_title=f"Sacherl {name} in Alleinlage mit Scheune",
            distance_driving_km=float(spec["distance_air_km"]) * 1.3,
            living_sqm=150.0,
            land_sqm=6_000.0,
            outbuildings=["Scheune"],
            year_built=1890,
            source_date=now - timedelta(days=3),
            last_verified=now - timedelta(days=3),
            verification_status=VerificationStatus.VERIFIED,
            condition="fair",
            lat=47.9,
            lon=11.8,
            **spec,
        )
        attach_source(session, prop, source)
        built[name] = prop
    session.commit()
    return built


# --------------------------------------------------------------------------- #
# The hash is what invalidates the cache
# --------------------------------------------------------------------------- #


def test_moving_a_slider_changes_the_profile_hash(
    wide: SearchProfile, narrow: SearchProfile, poorer: SearchProfile
) -> None:
    hashes = {wide.profile_hash, narrow.profile_hash, poorer.profile_hash}
    assert len(hashes) == 3
    assert wide.profile_hash == SearchProfile().profile_hash  # stable, not random


# --------------------------------------------------------------------------- #
# Direction of travel
# --------------------------------------------------------------------------- #


class TestDistanceSlider:
    def test_a_far_property_loses_its_geography_points(
        self, estate, wide: SearchProfile, narrow: SearchProfile, now: datetime
    ) -> None:
        far = estate["far"]
        before = score_property(far, wide, now=now)
        after = score_property(far, narrow, now=now)

        assert before.breakdown["fit"]["geography_score"] == 10.0
        assert after.breakdown["fit"]["geography_score"] == 0.0
        assert after.fit_score < before.fit_score
        assert after.final_score < before.final_score

    def test_a_far_property_falls_outside_the_new_radius(
        self, estate, wide: SearchProfile, narrow: SearchProfile, now: datetime
    ) -> None:
        far = estate["far"]
        assert score_property(far, wide, now=now).rejected is False
        after = score_property(far, narrow, now=now)
        assert after.rejected is True
        assert REJECT_AIR_DISTANCE in after.reject_reasons

    def test_a_near_property_is_judged_against_the_smaller_world(
        self, estate, wide: SearchProfile, narrow: SearchProfile, now: datetime
    ) -> None:
        """25 km is 31% of 80 km but 63% of 40 km - the same place, measured
        against a smaller world, is relatively less central."""
        near = estate["near"]
        before = score_property(near, wide, now=now)
        after = score_property(near, narrow, now=now)
        assert before.breakdown["fit"]["geography_score"] == 15.0
        assert after.breakdown["fit"]["geography_score"] == 13.0
        assert after.final_score < before.final_score


class TestBudgetSlider:
    def test_the_purchase_bands_follow_the_total_budget(
        self, wide: SearchProfile, poorer: SearchProfile
    ) -> None:
        assert wide.budget.effective_purchase_target_max == 750_000
        assert poorer.budget.effective_purchase_target_max == 500_000
        assert poorer.budget.effective_total_hard_max == 1_000_000

    def test_the_same_property_scores_worse_on_a_smaller_budget(
        self, estate, wide: SearchProfile, poorer: SearchProfile, now: datetime
    ) -> None:
        near = estate["near"]
        before = score_property(near, wide, now=now)
        after = score_property(near, poorer, now=now)

        assert after.breakdown["fit"]["price_score"] < before.breakdown["fit"]["price_score"]
        assert after.deal_score < before.deal_score
        assert after.final_score < before.final_score

    def test_what_was_affordable_becomes_unaffordable(
        self, estate, wide: SearchProfile, poorer: SearchProfile, now: datetime
    ) -> None:
        near = estate["near"]
        assert score_property(near, wide, now=now).rejected is False
        after = score_property(near, poorer, now=now)
        assert after.rejected is True
        assert REJECT_TOTAL_COST in after.reject_reasons
        assert after.capital_risk == "extreme"

    def test_nothing_about_the_property_itself_changed(
        self, estate, wide: SearchProfile, poorer: SearchProfile
    ) -> None:
        """The cost model depends on facts, not on what the user can afford."""
        from hofradar.costmodel import estimate_costs

        near = estate["near"]
        assert estimate_costs(near, wide).total_mid == estimate_costs(near, poorer).total_mid


# --------------------------------------------------------------------------- #
# Caching: one independent set of Score rows per profile
# --------------------------------------------------------------------------- #


class TestRescoreAll:
    def _rows(self, session, profile: SearchProfile) -> dict[int, Score]:
        return {
            row.property_id: row
            for row in session.scalars(
                select(Score).where(Score.profile_hash == profile.profile_hash)
            )
        }

    def test_it_writes_one_score_and_one_cost_row_per_property(
        self, session, estate, wide: SearchProfile
    ) -> None:
        assert rescore_all(session, wide) == len(estate)
        assert session.scalar(select(func.count()).select_from(Score)) == len(estate)
        assert session.scalar(select(func.count()).select_from(CostEstimate)) == len(estate)

    def test_it_is_idempotent(self, session, estate, wide: SearchProfile) -> None:
        assert rescore_all(session, wide) == len(estate)
        assert rescore_all(session, wide) == 0
        assert session.scalar(select(func.count()).select_from(Score)) == len(estate)

    def test_only_dirty_false_recomputes_everything(
        self, session, estate, wide: SearchProfile
    ) -> None:
        rescore_all(session, wide)
        assert rescore_all(session, wide, only_dirty=False) == len(estate)
        assert session.scalar(select(func.count()).select_from(Score)) == len(estate)

    def test_a_changed_property_becomes_dirty_again(
        self, session, estate, wide: SearchProfile
    ) -> None:
        rescore_all(session, wide)
        near = estate["near"]
        near.price = 300_000
        session.commit()
        assert rescore_all(session, wide) == 1

    def test_a_second_profile_gets_its_own_untouched_set_of_rows(
        self, session, estate, wide: SearchProfile, narrow: SearchProfile
    ) -> None:
        assert rescore_all(session, wide) == len(estate)
        before = {
            pid: (row.final_score, row.fit_score, row.rejected, row.updated_at)
            for pid, row in self._rows(session, wide).items()
        }

        assert rescore_all(session, narrow) == len(estate)
        assert session.scalar(select(func.count()).select_from(Score)) == 2 * len(estate)

        after = {
            pid: (row.final_score, row.fit_score, row.rejected, row.updated_at)
            for pid, row in self._rows(session, wide).items()
        }
        assert after == before, "wide-profile scores must survive a rescore under another profile"

        narrow_rows = self._rows(session, narrow)
        far_id = estate["far"].id
        assert narrow_rows[far_id].final_score < before[far_id][0]
        assert narrow_rows[far_id].rejected is True
        assert before[far_id][2] is False

    def test_switching_back_reuses_the_cached_scores(
        self, session, estate, wide: SearchProfile, poorer: SearchProfile
    ) -> None:
        rescore_all(session, wide)
        rescore_all(session, poorer)
        # Nothing changed about the properties, so going back is free.
        assert rescore_all(session, wide) == 0
        assert len(self._rows(session, wide)) == len(estate)
        assert len(self._rows(session, poorer)) == len(estate)


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #


class TestRankedProperties:
    def test_it_ranks_by_final_score_and_hides_rejects(
        self, session, estate, wide: SearchProfile
    ) -> None:
        rescore_all(session, wide)
        rows = ranked_properties(session, wide)
        scores = [score.final_score for _, score in rows]
        assert scores == sorted(scores, reverse=True)
        assert all(score.rejected is False for _, score in rows)

    def test_it_only_sees_its_own_profile(
        self, session, estate, wide: SearchProfile, narrow: SearchProfile
    ) -> None:
        rescore_all(session, wide)
        rescore_all(session, narrow)
        wide_ids = {prop.id for prop, _ in ranked_properties(session, wide)}
        narrow_ids = {prop.id for prop, _ in ranked_properties(session, narrow)}
        assert estate["far"].id in wide_ids
        assert estate["far"].id not in narrow_ids

    def test_limit_and_include_rejected(
        self, session, estate, narrow: SearchProfile
    ) -> None:
        rescore_all(session, narrow)
        assert len(ranked_properties(session, narrow, limit=1)) == 1
        assert len(ranked_properties(session, narrow, include_rejected=True)) == len(estate)

    @pytest.mark.parametrize(
        ("filters", "expected"),
        [
            ({"town": "Bad Feilnbach"}, 3),
            ({"town": "Nowhere"}, 0),
            ({"min_land_sqm": 5_000}, 3),
            ({"min_land_sqm": 7_000}, 0),
            ({"max_price": 500_000}, 2),
            ({"status": ListingStatus.ACTIVE}, 3),
            ({"status": ListingStatus.SOLD}, 0),
            ({"user_state": "shortlist"}, 0),
            ({"verified_only": True}, 3),
            ({"has_outbuildings": True}, 3),
            # only "pricey" pushes the all-in total past the 1.2M budget slider
            ({"flags": ["OVER_BUDGET"]}, 1),
            ({"flags": ["SANIERUNGSRISIKO"]}, 0),
            ({"q": "Feilnbach"}, 3),
            ({"q": "Nowhere"}, 0),
        ],
    )
    def test_filters(
        self, session, estate, wide: SearchProfile, filters: dict, expected: int
    ) -> None:
        rescore_all(session, wide)
        assert len(ranked_properties(session, wide, filters=filters)) == expected

    def test_status_alive_means_not_gone_rather_than_a_status_named_alive(
        self, session, estate, wide: SearchProfile
    ) -> None:
        """The control panel offers „Nur aktive"; no row ever carries that word."""
        rescore_all(session, wide)
        estate["far"].listing_status = ListingStatus.SOLD
        session.commit()
        assert len(ranked_properties(session, wide, filters={"status": "alive"})) == 2

    def test_a_hidden_property_is_ranked_only_when_asked_for(
        self, session, estate, wide: SearchProfile
    ) -> None:
        """Triage's archive is a reader-facing hide, not a scoring gate."""
        rescore_all(session, wide)
        estate["near"].user_state = "archived"
        session.commit()

        assert len(ranked_properties(session, wide)) == len(estate) - 1
        assert len(ranked_properties(session, wide, include_hidden=True)) == len(estate)
        # A triage verdict that is not a hide leaves the ranking alone.
        estate["near"].user_state = "rejected"
        session.commit()
        assert len(ranked_properties(session, wide)) == len(estate)

    def test_an_unknown_filter_is_an_error(
        self, session, estate, wide: SearchProfile
    ) -> None:
        rescore_all(session, wide)
        with pytest.raises(ValueError, match="unsupported ranking filter"):
            ranked_properties(session, wide, filters={"colour": "blue"})
