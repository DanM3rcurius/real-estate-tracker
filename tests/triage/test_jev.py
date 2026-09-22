"""The System One call: the request we would make, and what we do with the answer.

Every outbound call is mocked with ``respx``; the assertions are on the request
that *would* have been made (endpoint, bearer token, model, the question
names) and on how the documented answer shape becomes evidence.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from hofradar.config import SearchProfile
from hofradar.contracts import NormalizedListing
from hofradar.triage import (
    DWELLING_FLAT,
    OFFER_RENT,
    JevTriage,
    TriageUnavailable,
    decide,
    verdict_from_evidence,
)
from hofradar.triage.jev import Q_DWELLING, Q_OFFER, Q_SUBSTANCE, SYSTEM_ONE_PATH, parse_verdict

ENDPOINT = "https://api.typesafe.ai" + SYSTEM_ONE_PATH


def _listing(**overrides) -> NormalizedListing:
    defaults = dict(
        source_key="stub",
        url="https://stub.invalid/objekt/1",
        title="Bauernhaus mit Stadel",
        description="Charmantes Bauernhaus, 1.400 warm, ab sofort frei.",
        price_raw="1.400 €",
        town="Bad Aibling",
    )
    defaults.update(overrides)
    return NormalizedListing(**defaults)


def _answer(offer="miete", p_rent=0.91, dwelling="haus", p_flat=0.05, substance=0.2) -> dict:
    return {
        "model": "jev-1.13.0",
        "answers": {
            Q_OFFER: {
                "type": "choice",
                "choice": offer,
                "confidence": 0.8,
                "probabilities": {"kauf": round(1 - p_rent - 0.02, 2), "miete": p_rent, "unklar": 0.02},
            },
            Q_DWELLING: {
                "type": "choice",
                "choice": dwelling,
                "confidence": 0.7,
                "probabilities": {"hofstelle": 0.3, "haus": 0.65 - p_flat, "wohnung": p_flat},
            },
            Q_SUBSTANCE: {"type": "noul", "noul": substance},
        },
        "usage": {"input_tokens": 120, "output_tokens": 30},
    }


@pytest.mark.asyncio
async def test_the_request_is_the_documented_system_one_call() -> None:
    triage = JevTriage("sk-test", model="jev-latest")
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(ENDPOINT).mock(return_value=httpx.Response(200, json=_answer()))
        verdict = await triage.classify(_listing())
    await triage.aclose()

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer sk-test"
    body = json.loads(request.content)
    assert body["model"] == "jev-latest"
    assert set(body["questions"]) == {Q_OFFER, Q_DWELLING, Q_SUBSTANCE}
    assert body["questions"][Q_OFFER]["type"] == "choice"
    assert OFFER_RENT in body["questions"][Q_OFFER]["criteria"]
    assert DWELLING_FLAT in body["questions"][Q_DWELLING]["criteria"]
    assert body["state"]["titel"] == "Bauernhaus mit Stadel"
    assert body["state"]["preis"] == "1.400 €"

    assert verdict is not None
    assert verdict.offer_kind == "miete"
    assert verdict.offer_probabilities["miete"] == pytest.approx(0.91)
    assert verdict.farm_substance == pytest.approx(0.2)
    assert triage.stats() == {"enabled": True, "model": "jev-latest", "asked": 1, "failed": 0}


@pytest.mark.asyncio
async def test_a_failed_call_is_counted_and_yields_no_verdict() -> None:
    triage = JevTriage("sk-test")
    with respx.mock:
        respx.post(ENDPOINT).mock(return_value=httpx.Response(500, text="boom"))
        assert await triage.classify(_listing()) is None
        respx.post(ENDPOINT).mock(return_value=httpx.Response(200, json={"answers": {}}))
        assert await triage.classify(_listing()) is None
    await triage.aclose()
    assert triage.stats()["asked"] == 2
    assert triage.stats()["failed"] == 2


def test_without_a_key_the_stage_is_unavailable(monkeypatch) -> None:
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(TriageUnavailable):
        JevTriage.from_env()


def test_from_env_reads_key_base_url_and_model(monkeypatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-env")
    monkeypatch.setenv("TYPESAFE_BASE_URL", "https://proxy.example/")
    monkeypatch.setenv("HOFRADAR_JEV_MODEL", "jev-preview")
    triage = JevTriage.from_env()
    assert triage.model == "jev-preview"
    assert triage._endpoint == "https://proxy.example" + SYSTEM_ONE_PATH


def test_evidence_round_trips_through_the_database_shape() -> None:
    verdict = parse_verdict(_answer())
    entry = verdict.to_evidence()
    assert entry["source"] == "jev"
    assert entry["confidence"] == pytest.approx(0.91)
    again = verdict_from_evidence(entry)
    assert again is not None
    assert again.offer_probabilities == verdict.offer_probabilities
    assert again.dwelling_kind == verdict.dwelling_kind
    assert verdict_from_evidence({"source": "llm"}) is None
    assert verdict_from_evidence(None) is None


def test_decide_thresholds_and_the_substance_escape_hatch() -> None:
    gates = SearchProfile().gates  # 0.85
    rental = parse_verdict(_answer(p_rent=0.9))
    assert decide(rental, gates, has_substance=True).reject_reason == OFFER_RENT

    doubtful = parse_verdict(_answer(p_rent=0.6))
    decision = decide(doubtful, gates, has_substance=False)
    assert decision.reject_reason is None
    assert decision.flags and decision.warnings

    flat = parse_verdict(_answer(offer="kauf", p_rent=0.02, dwelling="wohnung", p_flat=0.9))
    assert decide(flat, gates, has_substance=False).reject_reason == DWELLING_FLAT
    assert decide(flat, gates, has_substance=True).reject_reason is None

    farm = parse_verdict(_answer(offer="kauf", p_rent=0.01, dwelling="hofstelle", p_flat=0.0))
    assert decide(farm, gates, has_substance=False) == decide(farm, gates, has_substance=True)
    assert decide(farm, gates, has_substance=False).flags == []
