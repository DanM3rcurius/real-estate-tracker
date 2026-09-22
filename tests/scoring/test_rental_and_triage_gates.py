"""A rental is never for sale, and a triage verdict is read through one rule.

Both gates exist because listings that were plainly not purchases reached the
top ten: a "Bauernhaus" at 1.800 EUR Kaltmiete parsed as the cheapest farm in
Bavaria, and 3-Zimmer flats from broker sitemaps with no keyword the negative
list knew. The rental gate is deliberately NOT subject to the substance
override that saves a farm from a stray "Neubau"; the triage flat verdict is.
"""

from __future__ import annotations

from hofradar.config import SearchProfile
from hofradar.db.enums import PriceType
from hofradar.db.models import Property
from hofradar.scoring import score_property
from hofradar.scoring.engine import REJECT_RENTAL, REJECT_TRIAGE
from hofradar.triage import DWELLING_FLAT, OFFER_RENT, TRIAGE_EVIDENCE_KEY
from hofradar.triage.rules import FLAG_TRIAGE_DOUBT


def _property(**overrides) -> Property:
    defaults = dict(
        public_id="hof-test",
        canonical_title="Testobjekt",
        town="Rosenheim",
        price=640_000.0,
        price_type=PriceType.ASKING,
        land_sqm=2_100.0,
        living_sqm=140.0,
        year_built=1924,
        distance_air_km=22.3,
        distance_driving_km=28.0,
        property_type="hofstelle",
        building_features=[],
        outbuildings=["stadel"],
        special_features=[],
        exclusion_flags=[],
        evidence={},
    )
    defaults.update(overrides)
    return Property(**defaults)


def _triage(offer: dict[str, float], dwelling: dict[str, float]) -> dict:
    return {
        "source": "jev",
        "confidence": 0.9,
        "observed_at": "2026-09-18T12:00:00+00:00",
        "model": "jev-1.13.0",
        "offer_kind": max(offer, key=offer.get),
        "offer_probabilities": offer,
        "dwelling_kind": max(dwelling, key=dwelling.get),
        "dwelling_probabilities": dwelling,
        "farm_substance": 0.5,
    }


def test_a_rental_is_rejected_whatever_substance_it_carries() -> None:
    prop = _property(price=1_800.0, price_type=PriceType.RENT, exclusion_flags=["mietobjekt"])
    result = score_property(prop, SearchProfile())
    assert result.rejected is True
    assert REJECT_RENTAL in result.reject_reasons


def test_a_sale_with_a_tenant_is_not_a_rental() -> None:
    result = score_property(_property(), SearchProfile())
    assert REJECT_RENTAL not in result.reject_reasons


def test_triage_rental_above_threshold_rejects() -> None:
    prop = _property(
        evidence={TRIAGE_EVIDENCE_KEY: _triage({"kauf": 0.05, "miete": 0.93}, {"hofstelle": 0.8})}
    )
    result = score_property(prop, SearchProfile())
    assert REJECT_TRIAGE[OFFER_RENT] in result.reject_reasons


def test_triage_rental_below_threshold_only_flags() -> None:
    prop = _property(
        evidence={TRIAGE_EVIDENCE_KEY: _triage({"kauf": 0.4, "miete": 0.6}, {"hofstelle": 0.8})}
    )
    result = score_property(prop, SearchProfile())
    assert REJECT_TRIAGE[OFFER_RENT] not in result.reject_reasons
    assert FLAG_TRIAGE_DOUBT in result.flags


def test_triage_flat_is_overridden_by_deterministic_substance() -> None:
    evidence = {TRIAGE_EVIDENCE_KEY: _triage({"kauf": 0.95}, {"wohnung": 0.9, "hofstelle": 0.1})}
    with_stadel = score_property(_property(evidence=evidence), SearchProfile())
    assert REJECT_TRIAGE[DWELLING_FLAT] not in with_stadel.reject_reasons
    assert FLAG_TRIAGE_DOUBT in with_stadel.flags

    bare = score_property(_property(evidence=evidence, outbuildings=[]), SearchProfile())
    assert REJECT_TRIAGE[DWELLING_FLAT] in bare.reject_reasons


def test_threshold_of_one_turns_the_reject_off_but_keeps_the_flag() -> None:
    profile = SearchProfile.model_validate({"gates": {"triage_reject_min_probability": 1.0}})
    prop = _property(
        evidence={TRIAGE_EVIDENCE_KEY: _triage({"kauf": 0.01, "miete": 0.99}, {"hofstelle": 0.8})}
    )
    result = score_property(prop, profile)
    assert not any(r.startswith("TRIAGE_") for r in result.reject_reasons)
    assert FLAG_TRIAGE_DOUBT in result.flags


def test_foreign_evidence_under_the_triage_key_is_ignored() -> None:
    prop = _property(evidence={TRIAGE_EVIDENCE_KEY: {"source": "somebody_else", "offer_kind": "miete"}})
    result = score_property(prop, SearchProfile())
    assert not any(r.startswith("TRIAGE_") for r in result.reject_reasons)
