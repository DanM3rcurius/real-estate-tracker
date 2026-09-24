"""The reader's Sanierungsstufe is stood-behind evidence: it can hard-reject.

``_apply_gates`` flags rather than rejects a total-cost breach when nothing
but the pessimistic age fallback produced the figure - defaulting a property
out of the shortlist would hide exactly the pre-1960 farmsteads this project
exists to find. A reader who has walked the building and set the tier
themselves has stood behind the number, so the same breach is a hard reject
instead (docs/DECISIONS.md entry 30).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from factories import make_property

from hofradar.config import SearchProfile
from hofradar.db.models import CostEstimate, Score
from hofradar.scoring import rescore_property, score_property
from hofradar.scoring.engine import FLAG_COST_INFERRED, REJECT_TOTAL_COST


@pytest.fixture()
def profile() -> SearchProfile:
    # A tight hard cap that a heavy renovation on a large farmstead blows
    # through, whichever tier ends up priced.
    return SearchProfile.model_validate({"budget": {"total_budget_hard_max": 200_000}})


def _expensive_property(**kwargs):
    return make_property(
        price=150_000,
        living_sqm=300,
        outbuildings=["Scheune", "Stall"],
        condition=None,
        year_built=1890,
        **kwargs,
    )


def test_age_fallback_over_budget_is_flagged_not_rejected(profile: SearchProfile) -> None:
    prop = _expensive_property()

    result = score_property(prop, profile)

    assert FLAG_COST_INFERRED in result.flags
    assert REJECT_TOTAL_COST not in result.reject_reasons


def test_reader_tier_over_budget_is_rejected_with_the_cost_reason(profile: SearchProfile) -> None:
    prop = _expensive_property(user_renovation_tier="heavy")

    result = score_property(prop, profile)

    assert REJECT_TOTAL_COST in result.reject_reasons
    assert FLAG_COST_INFERRED not in result.flags
    assert result.rejected


class TestRescoreProperty:
    def test_writes_cost_and_score_reflecting_the_override(self, session, now: datetime) -> None:
        prop = make_property(
            session,
            price=150_000,
            living_sqm=300,
            outbuildings=["Scheune", "Stall"],
            condition=None,
            year_built=1890,
            user_renovation_tier="heavy",
        )
        profile = SearchProfile.model_validate({"budget": {"total_budget_hard_max": 200_000}})

        result = rescore_property(session, prop, profile, now=now)

        cost_row = session.query(CostEstimate).filter_by(property_id=prop.id).one()
        score_row = (
            session.query(Score)
            .filter_by(property_id=prop.id, profile_hash=profile.profile_hash)
            .one()
        )
        assert cost_row.renovation_tier == "heavy"
        assert score_row.rejected is True
        assert REJECT_TOTAL_COST in score_row.reject_reasons
        assert result.rejected is True

    def test_changing_the_override_updates_the_same_rows(self, session, now: datetime) -> None:
        prop = make_property(
            session,
            price=150_000,
            living_sqm=300,
            outbuildings=["Scheune", "Stall"],
            condition=None,
            year_built=1890,
            user_renovation_tier="heavy",
        )
        profile = SearchProfile.model_validate({"budget": {"total_budget_hard_max": 200_000}})

        rescore_property(session, prop, profile, now=now)
        cost_id_before = session.query(CostEstimate).filter_by(property_id=prop.id).one().id
        score_id_before = (
            session.query(Score)
            .filter_by(property_id=prop.id, profile_hash=profile.profile_hash)
            .one()
            .id
        )

        prop.user_renovation_tier = "light"
        session.flush()
        rescore_property(session, prop, profile, now=now)

        cost_rows = session.query(CostEstimate).filter_by(property_id=prop.id).all()
        score_rows = (
            session.query(Score)
            .filter_by(property_id=prop.id, profile_hash=profile.profile_hash)
            .all()
        )
        assert len(cost_rows) == 1
        assert len(score_rows) == 1
        assert cost_rows[0].id == cost_id_before
        assert score_rows[0].id == score_id_before
        assert cost_rows[0].renovation_tier == "light"


def test_an_edit_that_moves_no_number_still_settles(session, now: datetime) -> None:
    """Pinning the tier the listing already implies changes no score value,
    so no UPDATE is emitted - the row must still be stamped newer than the
    property, or ``rescore_all`` rescores it on every page load forever."""
    from hofradar.scoring import rescore_all

    prop = make_property(session, condition="renovierungsbeduerftig", year_built=1990)
    session.commit()
    profile = SearchProfile()
    rescore_all(session, profile, only_dirty=False, now=now)

    prop.user_renovation_tier = "medium"  # what the listing implies anyway
    session.commit()
    assert rescore_all(session, profile, now=now) == 1
    assert rescore_all(session, profile, now=now) == 0
