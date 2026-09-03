"""hidden_score, freshness_score and confidence_score."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from factories import attach_source, make_property, make_source

from hofradar.config import SearchProfile
from hofradar.db.enums import (
    ListingStatus,
    PriceType,
    SourceRole,
    VerificationStatus,
)
from hofradar.scoring import confidence_score, freshness_score, hidden_score
from hofradar.scoring.signals import NO_EVIDENCE_DATE_FLAG, STALE_FLAG


@pytest.fixture()
def profile() -> SearchProfile:
    return SearchProfile()


def ago(now: datetime, days: float) -> datetime:
    return now - timedelta(days=days)


# --------------------------------------------------------------------------- #
# freshness
# --------------------------------------------------------------------------- #


class TestFreshness:
    @pytest.mark.parametrize(
        ("days", "expected"),
        [(0, 100.0), (7, 100.0), (8, 90.0), (14, 90.0), (15, 75.0), (30, 75.0),
         (31, 50.0), (60, 50.0), (61, 30.0), (90, 30.0), (91, 10.0), (180, 10.0),
         (181, 0.0), (900, 0.0)],
    )
    def test_bands(self, session, now: datetime, days: int, expected: float) -> None:
        prop = make_property(session, source_date=ago(now, days), first_seen=ago(now, days))
        assert freshness_score(prop, now)[0] == expected

    def test_a_discovery_source_can_never_raise_freshness(self, session, now: datetime) -> None:
        """The whole point: re-crawling a dead listing must not make it look new."""
        discovery = make_source(session, key="bing", role=SourceRole.DISCOVERY, reliability=0.9)
        prop = make_property(
            session,
            source_date=None,
            last_seen=now,
            #: Even if some other stage optimistically stamped these, a
            #: discovery source is not allowed to stand behind them.
            last_verified=ago(now, 1),
            verification_status=VerificationStatus.VERIFIED,
        )
        attach_source(session, prop, discovery, source_date=ago(now, 1))

        score, breakdown = freshness_score(prop, now)
        assert score == 0.0
        assert NO_EVIDENCE_DATE_FLAG in breakdown["flags"]
        assert breakdown["candidates"] == {}

    def test_the_same_property_is_fresh_once_a_primary_source_verifies_it(
        self, session, now: datetime
    ) -> None:
        primary = make_source(session, key="portal", role=SourceRole.PRIMARY)
        prop = make_property(
            session,
            source_date=None,
            last_verified=ago(now, 1),
            verification_status=VerificationStatus.VERIFIED,
        )
        attach_source(session, prop, primary)
        score, breakdown = freshness_score(prop, now)
        assert score == 100.0
        assert breakdown["best_evidence_source"] == "property.last_verified"

    def test_the_sellers_own_date_wins_over_our_bookkeeping(
        self, session, now: datetime
    ) -> None:
        prop = make_property(session, source_date=ago(now, 20), first_seen=ago(now, 400))
        score, breakdown = freshness_score(prop, now)
        assert score == 75.0
        assert breakdown["best_evidence_source"] == "property.source_date"


# --------------------------------------------------------------------------- #
# hidden
# --------------------------------------------------------------------------- #


class TestHidden:
    def test_a_well_marketed_portal_listing_scores_low(
        self, session, profile: SearchProfile, now: datetime
    ) -> None:
        source = make_source(session, key="portal")
        prop = make_property(
            session,
            description="Sehr gepflegtes Anwesen. " * 40,
            price_type=PriceType.ASKING,
            first_seen=ago(now, 3),
        )
        attach_source(session, prop, source, contact_kind="broker", contact_detail="Makler GmbH")
        score, breakdown = hidden_score(prop, profile, now)
        assert score == 0.0
        assert breakdown["signals"] == {}

    def test_the_signals_are_itemised(
        self, session, profile: SearchProfile, now: datetime
    ) -> None:
        local = make_source(session, key="gemeindeblatt", role=SourceRole.LOCAL, reliability=0.6)
        prop = make_property(
            session,
            description="Chiffre 4711, Verkauf von privat, kein Makler.",
            price_type=PriceType.NEGOTIABLE,
            price_reduction_count=3,
            is_private_seller=True,
            is_foreclosure=True,
            is_off_market_signal=True,
            first_seen=ago(now, 200),
        )
        attach_source(session, prop, local, contact_kind="private", contact_detail="Fam. Huber")
        score, breakdown = hidden_score(prop, profile, now)
        signals = breakdown["signals"]
        assert signals["privatverkauf"] == 15.0
        assert signals["preis_vb"] == 8.0
        assert signals["long_online"] == 10.0
        assert signals["price_reductions"] == 12.0
        assert signals["small_local_source"] == 8.0
        assert signals["chiffre"] == 15.0
        assert signals["kein_makler"] == 5.0
        assert signals["direct_owner_contact"] == 10.0
        assert signals["zwangsversteigerung"] == 15.0
        assert signals["off_market_hint"] == 20.0
        assert score == 100.0  # clamped

    def test_price_on_request_is_its_own_signal(
        self, session, profile: SearchProfile, now: datetime
    ) -> None:
        prop = make_property(session, price=None, price_type=PriceType.ON_REQUEST)
        assert hidden_score(prop, profile, now)[1]["signals"]["preis_auf_anfrage"] == 8.0


class TestTwoYearsOnline:
    """Long online is a hidden-gem signal AND a warning. Both must be recorded."""

    @pytest.fixture()
    def veteran(self, session, now: datetime):
        source = make_source(session, key="portal")
        prop = make_property(
            session,
            first_seen=ago(now, 800),
            source_date=ago(now, 5),
            last_verified=ago(now, 5),
            verification_status=VerificationStatus.VERIFIED,
        )
        attach_source(session, prop, source)
        return prop

    def test_hidden_gets_the_bonus_and_the_penalty(
        self, veteran, profile: SearchProfile, now: datetime
    ) -> None:
        score, breakdown = hidden_score(veteran, profile, now)
        assert breakdown["signals"]["long_online"] == 10.0
        assert breakdown["signals"]["stale_penalty"] == -10.0
        assert breakdown["is_stale"] is True
        assert STALE_FLAG in breakdown["flags"]
        assert score >= 0.0

    def test_freshness_reflects_the_stale_penalty(self, veteran, now: datetime) -> None:
        score, breakdown = freshness_score(veteran, now)
        assert breakdown["base_score"] == 100.0
        assert breakdown["stale_penalty"] == -10.0
        assert STALE_FLAG in breakdown["flags"]
        assert score == 90.0

    def test_a_listing_online_for_one_year_is_not_yet_stale(
        self, session, profile: SearchProfile, now: datetime
    ) -> None:
        prop = make_property(session, first_seen=ago(now, 365), source_date=ago(now, 5))
        _, breakdown = hidden_score(prop, profile, now)
        assert breakdown["signals"]["long_online"] == 10.0
        assert "stale_penalty" not in breakdown["signals"]


# --------------------------------------------------------------------------- #
# confidence
# --------------------------------------------------------------------------- #


class TestConfidence:
    def test_a_fully_evidenced_property_scores_high(self, session, now: datetime) -> None:
        primary = make_source(session, key="portal", role=SourceRole.PRIMARY, reliability=0.95)
        local = make_source(session, key="blatt", role=SourceRole.LOCAL, reliability=0.7)
        prop = make_property(
            session,
            geo_precision="exact",
            price_type=PriceType.ASKING,
            condition="fair",
            lat=47.9,
            lon=11.8,
            last_verified=ago(now, 2),
            verification_status=VerificationStatus.VERIFIED,
        )
        attach_source(session, prop, primary)
        attach_source(session, prop, local, is_best=False)
        score, breakdown = confidence_score(prop, now)
        assert score > 90
        assert breakdown["components"]["availability"] == 100.0
        assert breakdown["source_count"] == 2

    def test_an_unverified_discovery_only_listing_scores_low(
        self, session, now: datetime
    ) -> None:
        discovery = make_source(session, key="cache", role=SourceRole.DISCOVERY, reliability=0.3)
        prop = make_property(
            session,
            price=None,
            price_type=PriceType.UNKNOWN,
            land_sqm=None,
            living_sqm=None,
            year_built=None,
            condition=None,
            town=None,
            lat=None,
            geo_precision="none",
            verification_status=VerificationStatus.UNVERIFIED,
            last_verified=None,
        )
        attach_source(session, prop, discovery)
        score, _ = confidence_score(prop, now)
        assert score < 30

    def test_a_removed_listing_has_no_availability(self, session, now: datetime) -> None:
        primary = make_source(session, key="portal", role=SourceRole.PRIMARY)
        prop = make_property(
            session, listing_status=ListingStatus.REMOVED, last_verified=ago(now, 1)
        )
        attach_source(session, prop, primary)
        assert confidence_score(prop, now)[1]["components"]["availability"] == 0.0

    def test_an_expired_listing_keeps_its_availability(self, session, now: datetime) -> None:
        """EXPIRED is a billing-cycle fact, not proof the farmstead is gone -
        it must not be in GONE_STATUSES, or a live listing's confidence would
        be zeroed out by an ad package simply running out."""
        primary = make_source(session, key="portal", role=SourceRole.PRIMARY)
        prop = make_property(
            session, listing_status=ListingStatus.EXPIRED, last_verified=ago(now, 1)
        )
        attach_source(session, prop, primary)
        assert confidence_score(prop, now)[1]["components"]["availability"] == 100.0

    def test_a_merge_flagged_for_review_caps_duplicate_certainty(
        self, session, now: datetime
    ) -> None:
        primary = make_source(session, key="portal", role=SourceRole.PRIMARY)
        other = make_source(session, key="portal2", role=SourceRole.PRIMARY)
        prop = make_property(session, exclusion_flags=["needs_review"])
        attach_source(session, prop, primary)
        attach_source(session, prop, other, is_best=False)
        assert confidence_score(prop, now)[1]["components"]["duplicate"] == 40.0

    def test_the_weights_are_the_documented_ones(self, session, now: datetime) -> None:
        prop = make_property(session)
        weights = confidence_score(prop, now)[1]["weights"]
        assert weights == {
            "source_reliability": 0.30,
            "availability": 0.20,
            "location": 0.20,
            "price": 0.15,
            "completeness": 0.10,
            "duplicate": 0.05,
        }
        assert sum(weights.values()) == pytest.approx(1.0)
