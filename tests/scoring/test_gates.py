"""The hard gates: what is rejected, what is merely held back, and why."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from factories import attach_source, make_property, make_source

from hofradar.config import SearchProfile
from hofradar.db.enums import ListingStatus, PriceType, SourceRole, VerificationStatus
from hofradar.scoring import ranked_properties, rescore_all, score_property
from hofradar.scoring.engine import (
    FLAG_DRIVING_UNVERIFIED,
    FLAG_SHORTLIST_BLOCKED,
    REJECT_AIR_DISTANCE,
    REJECT_DRIVING_DISTANCE,
    REJECT_DRIVING_UNVERIFIED,
    REJECT_LISTING_GONE,
    REJECT_OBSERVATION_ONLY,
    REJECT_PRICE,
    UNROUTED_CONFIDENCE_CEILING,
)


@pytest.fixture()
def profile() -> SearchProfile:
    return SearchProfile()


def ago(now: datetime, days: float) -> datetime:
    return now - timedelta(days=days)


def well_evidenced(session, now: datetime, **kwargs):
    """A property nobody can reject for lack of evidence."""
    source = make_source(session, key=f"portal-{kwargs.get('public_id', id(kwargs))}")
    prop = make_property(
        session,
        last_verified=ago(now, 2),
        verification_status=VerificationStatus.VERIFIED,
        source_date=ago(now, 2),
        condition="fair",
        lat=47.9,
        lon=11.8,
        **kwargs,
    )
    attach_source(session, prop, source)
    return prop


class TestDistanceGates:
    def test_beyond_the_air_radius_is_rejected(
        self, session, profile: SearchProfile, now: datetime
    ) -> None:
        prop = well_evidenced(session, now, distance_air_km=81.0)
        result = score_property(prop, profile, now=now)
        assert result.rejected is True
        assert REJECT_AIR_DISTANCE in result.reject_reasons

    def test_beyond_the_hard_driving_limit_is_rejected(
        self, session, profile: SearchProfile, now: datetime
    ) -> None:
        assert profile.radius.effective_driving_hard == 116.0
        prop = well_evidenced(session, now, distance_air_km=70.0, distance_driving_km=120.0)
        result = score_property(prop, profile, now=now)
        assert REJECT_DRIVING_DISTANCE in result.reject_reasons

    def test_an_unrouted_property_is_held_back_not_rejected(
        self, session, profile: SearchProfile, now: datetime
    ) -> None:
        prop = well_evidenced(session, now, distance_driving_km=None)
        result = score_property(prop, profile, now=now)
        assert result.rejected is False
        assert FLAG_DRIVING_UNVERIFIED in result.flags
        assert result.confidence_score == UNROUTED_CONFIDENCE_CEILING
        assert FLAG_SHORTLIST_BLOCKED in result.flags

    def test_reject_unrouted_makes_it_a_rejection(self, session, now: datetime) -> None:
        profile = SearchProfile.model_validate({"gates": {"reject_unrouted": True}})
        prop = well_evidenced(session, now, distance_driving_km=None)
        result = score_property(prop, profile, now=now)
        assert result.rejected is True
        assert REJECT_DRIVING_UNVERIFIED in result.reject_reasons


class TestPriceAndStatusGates:
    def test_above_the_hard_price_ceiling_is_rejected(
        self, session, profile: SearchProfile, now: datetime
    ) -> None:
        prop = well_evidenced(session, now, price=profile.budget.effective_purchase_hard_max + 1)
        result = score_property(prop, profile, now=now)
        assert REJECT_PRICE in result.reject_reasons

    @pytest.mark.parametrize("status", [ListingStatus.REMOVED, ListingStatus.SOLD])
    def test_a_gone_listing_is_rejected(
        self, session, profile: SearchProfile, now: datetime, status: str
    ) -> None:
        prop = well_evidenced(session, now, listing_status=status)
        result = score_property(prop, profile, now=now)
        assert REJECT_LISTING_GONE in result.reject_reasons

    def test_the_removal_gate_can_be_switched_off(self, session, now: datetime) -> None:
        profile = SearchProfile.model_validate({"gates": {"reject_removed": False}})
        prop = well_evidenced(session, now, listing_status=ListingStatus.REMOVED)
        result = score_property(prop, profile, now=now)
        assert REJECT_LISTING_GONE not in result.reject_reasons

    def test_an_expired_advert_is_not_treated_as_gone(
        self, session, profile: SearchProfile, now: datetime
    ) -> None:
        """EXPIRED means a newspaper's ad window ran out, not that the
        farmstead sold or was withdrawn - it must not be in GONE_STATUSES,
        or a live listing would drop out of the ranking on a billing timer."""
        prop = well_evidenced(session, now, listing_status=ListingStatus.EXPIRED)
        result = score_property(prop, profile, now=now)
        assert REJECT_LISTING_GONE not in result.reject_reasons


class TestConfidenceGates:
    @pytest.fixture()
    def observation_only(self, session, now: datetime):
        """Seen once, by a cache, with almost no facts attached."""
        discovery = make_source(session, key="cache", role=SourceRole.DISCOVERY, reliability=0.3)
        prop = make_property(
            session,
            price=None,
            price_type=PriceType.UNKNOWN,
            land_sqm=None,
            living_sqm=None,
            year_built=None,
            town=None,
            lat=None,
            geo_precision="none",
            distance_driving_km=40.0,
            verification_status=VerificationStatus.UNVERIFIED,
            last_verified=None,
        )
        attach_source(session, prop, discovery)
        return prop

    @pytest.fixture()
    def borderline(self, session, now: datetime):
        """Believable enough to keep, not believable enough to shortlist."""
        local = make_source(session, key="blatt", role=SourceRole.LOCAL, reliability=0.5)
        prop = make_property(
            session,
            geo_precision="town",
            price_type=PriceType.NEGOTIABLE,
            lat=None,
            condition=None,
            distance_driving_km=40.0,
            source_date=ago(now, 3),
            last_verified=ago(now, 20),
            verification_status=VerificationStatus.VERIFIED,
        )
        attach_source(session, prop, local)
        return prop

    def test_below_min_confidence_to_keep_is_observation_only(
        self, observation_only, profile: SearchProfile, now: datetime
    ) -> None:
        result = score_property(observation_only, profile, now=now)
        assert result.confidence_score < profile.gates.min_confidence_to_keep
        assert result.rejected is True
        assert REJECT_OBSERVATION_ONLY in result.reject_reasons

    def test_it_is_still_kept_in_the_database(
        self, session, observation_only, profile: SearchProfile
    ) -> None:
        assert rescore_all(session, profile) == 1
        assert ranked_properties(session, profile) == []
        kept = ranked_properties(session, profile, include_rejected=True)
        assert [prop.id for prop, _ in kept] == [observation_only.id]

    def test_between_the_two_thresholds_it_is_kept_but_not_shortlistable(
        self, borderline, profile: SearchProfile, now: datetime
    ) -> None:
        result = score_property(borderline, profile, now=now)
        assert (
            profile.gates.min_confidence_to_keep
            <= result.confidence_score
            < profile.gates.min_confidence_for_shortlist
        )
        assert result.rejected is False
        assert FLAG_SHORTLIST_BLOCKED in result.flags

    def test_a_blocked_property_never_outranks_a_shortlistable_one(
        self, session, borderline, profile: SearchProfile, now: datetime
    ) -> None:
        """Even when its raw final score is higher, the ranker holds it back."""
        weak_but_trusted = well_evidenced(
            session,
            now,
            canonical_title="Wohnhaus ohne Nebengebaeude",
            description="Schlichtes Wohnhaus im Ort.",
            special_features=[],
            outbuildings=[],
            building_features=[],
            price=740_000,
            land_sqm=1_200,
            year_built=1975,
        )
        rescore_all(session, profile)

        by_id = {prop.id: score for prop, score in ranked_properties(session, profile)}
        assert by_id[borderline.id].final_score > by_id[weak_but_trusted.id].final_score

        order = [prop.id for prop, _ in ranked_properties(session, profile)]
        assert order.index(weak_but_trusted.id) < order.index(borderline.id)
