"""Rentals and flats leave the crawl loop counted, and the triage says whether it ran.

Same harness as ``test_rejected_pages_are_counted.py``: the real
``run_pipeline`` over a stub adapter. Three listings: a farm for sale, a
rental the regex catches on its own, and a rental only the System One triage
recognises (no rent vocabulary, a warm rent with no label). Outbound calls
are mocked with ``respx``; the geo stage is the gazetteer.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import contextmanager
from datetime import date

import httpx
import pytest
import respx
from sqlalchemy import select

import hofradar.pipeline.runner as runner_module
import hofradar.sources as sources_module
from hofradar.config import AppConfig, KeywordConfig, SearchProfile, SourceConfig
from hofradar.contracts import RawListing
from hofradar.db.enums import PriceType, RunStage
from hofradar.db.models import Property, PropertySource
from hofradar.pipeline.runner import REJECT_RENTAL, REJECT_TRIAGE
from hofradar.sources.base import SourceAdapter
from hofradar.triage import OFFER_RENT, TRIAGE_EVIDENCE_KEY
from hofradar.triage.jev import Q_DWELLING, Q_OFFER, Q_SUBSTANCE, SYSTEM_ONE_PATH

_ADAPTER_KEY = "rental-stub"
_FARM_URL = "https://stub.invalid/objekt/hofstelle"
_RENTAL_URL = "https://stub.invalid/objekt/bauernhaus-miete"
_SUBTLE_URL = "https://stub.invalid/objekt/bauernhaus-warm"
ENDPOINT = "https://api.typesafe.ai" + SYSTEM_ONE_PATH


class _RentalStubAdapter(SourceAdapter):
    key = _ADAPTER_KEY

    async def discover(
        self, profile: SearchProfile, keywords: KeywordConfig
    ) -> AsyncIterator[RawListing]:
        self.begin_enumeration()
        yield RawListing(
            source_key=self.key,
            url=_FARM_URL,
            title="Hofstelle in Bad Aibling",
            description="Hofstelle mit Stadel in Bad Aibling.",
            town="Bad Aibling",
            postcode="83043",
            price_raw="420.000 EUR",
        )
        yield RawListing(
            source_key=self.key,
            url=_RENTAL_URL,
            title="Bauernhaus mit Stadel zu vermieten",
            description="Kaltmiete 1.800 EUR, Kaution drei Monatsmieten.",
            town="Bad Aibling",
            postcode="83043",
            price_raw="1.800 EUR",
        )
        yield RawListing(
            source_key=self.key,
            url=_SUBTLE_URL,
            title="Bauernhaus in Bad Aibling",
            description="Charmantes Bauernhaus, 1.400 warm, ab sofort frei.",
            town="Bad Aibling",
            postcode="83043",
            price_raw="1.400 EUR",
        )

    async def fetch_detail(self, url: str) -> RawListing | None:  # pragma: no cover - unused
        raise NotImplementedError


def _app_config() -> AppConfig:
    return AppConfig(
        profile=SearchProfile(),
        keywords=KeywordConfig(
            core=["Hofstelle", "Bauernhaus"],
            buildings=["Stadel"],
            hidden_phrases=[],
            regional=[],
            negative=["Eigentumswohnung"],
        ),
        sources=[
            SourceConfig(
                key=_ADAPTER_KEY,
                name="Rental stub",
                role="primary",
                adapter=_ADAPTER_KEY,
                base_url="https://stub.invalid",
                reliability=0.8,
                enabled=True,
                rate_limit_seconds=0.0,
                respect_robots=False,
                terms_checked_at=date(2026, 9, 3),
                terms_excerpt="Test stub - not a real source.",
                options={},
            )
        ],
    )


@contextmanager
def _session_scope_over(session):
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise


def _jev_answer(request: httpx.Request) -> httpx.Response:
    """The model reads the warm rent; the farm is a farm."""
    state = json.loads(request.content)["state"]
    rental = "warm" in state["beschreibung"]
    p_rent = 0.94 if rental else 0.02
    return httpx.Response(
        200,
        json={
            "model": "jev-1.13.0",
            "answers": {
                Q_OFFER: {
                    "type": "choice",
                    "choice": "miete" if rental else "kauf",
                    "confidence": 0.8,
                    "probabilities": {"kauf": 1 - p_rent, "miete": p_rent, "unklar": 0.0},
                },
                Q_DWELLING: {
                    "type": "choice",
                    "choice": "haus" if rental else "hofstelle",
                    "confidence": 0.7,
                    "probabilities": {"hofstelle": 0.1 if rental else 0.9, "haus": 0.9 if rental else 0.1},
                },
                Q_SUBSTANCE: {"type": "noul", "noul": 0.1 if rental else 0.9},
            },
            "usage": {"input_tokens": 100, "output_tokens": 20},
        },
    )


def _install(monkeypatch, db_session) -> None:
    monkeypatch.setenv("HOFRADAR_OFFLINE", "1")
    monkeypatch.setattr(runner_module, "load_config", _app_config)
    monkeypatch.setattr(runner_module, "session_scope", lambda: _session_scope_over(db_session))
    monkeypatch.setitem(sources_module.ADAPTERS, _ADAPTER_KEY, _RentalStubAdapter)


def _by_url(session) -> dict[str, Property]:
    """Properties keyed by the URL the stub source knows them under."""
    rows = session.execute(
        select(PropertySource.url, Property).join(Property, Property.id == PropertySource.property_id)
    )
    return {url: prop for url, prop in rows}


def _normalize_entry(run) -> dict:
    entries = [e for e in run.log if e["stage"] == str(RunStage.NORMALIZE)]
    assert entries, "the run must say what it threw away"
    return entries[-1]


@pytest.mark.asyncio
async def test_without_a_key_the_regex_rental_is_counted_and_the_triage_is_logged_absent(
    db_session, monkeypatch
) -> None:
    _install(monkeypatch, db_session)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)

    with respx.mock(assert_all_called=False) as mock:
        jev = mock.post(ENDPOINT)
        run = await runner_module.run_pipeline(source_keys=[_ADAPTER_KEY], trigger="test")

    assert run.status == "ok"
    assert not jev.called, "no key, no call"
    urls = set(_by_url(db_session))
    assert _RENTAL_URL not in urls
    assert _FARM_URL in urls
    # The subtle rental is the regex's blind spot - without the triage it is remembered.
    assert _SUBTLE_URL in urls

    entry = _normalize_entry(run)
    assert entry["reasons"] == {REJECT_RENTAL: 1}
    assert entry["triage"] == {"enabled": False}


@pytest.mark.asyncio
async def test_with_a_key_the_triage_catches_the_subtle_rental_and_leaves_evidence(
    db_session, monkeypatch
) -> None:
    _install(monkeypatch, db_session)
    monkeypatch.setenv("TYPESAFE_API_KEY", "sk-test")

    with respx.mock(assert_all_called=True) as mock:
        jev = mock.post(ENDPOINT).mock(side_effect=_jev_answer)
        run = await runner_module.run_pipeline(source_keys=[_ADAPTER_KEY], trigger="test")

    assert run.status == "ok"
    # The regex rental never reached the model: deterministic rejects come first.
    assert jev.call_count == 2
    props = _by_url(db_session)
    assert set(props) == {_FARM_URL}

    farm = props[_FARM_URL]
    assert farm.evidence[TRIAGE_EVIDENCE_KEY]["source"] == "jev"
    assert farm.evidence[TRIAGE_EVIDENCE_KEY]["offer_kind"] == "kauf"
    assert farm.price_type == PriceType.ASKING

    entry = _normalize_entry(run)
    assert entry["reasons"] == {REJECT_RENTAL: 1, f"{REJECT_TRIAGE}:{OFFER_RENT}": 1}
    assert entry["triage"] == {"enabled": True, "model": "jev-latest", "asked": 2, "failed": 0}


@pytest.mark.asyncio
async def test_a_known_rental_is_ingested_so_the_row_learns_and_the_gate_retires_it(
    db_session, monkeypatch, make_property, make_source
) -> None:
    """A row remembered before the gate existed must not stay on the radar
    behind a bare ``continue``: it goes through ingest and picks up the fact."""
    from hofradar.scoring import ranked_properties

    _install(monkeypatch, db_session)
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    source = make_source(key=_ADAPTER_KEY)
    old = make_property(
        canonical_title="Bauernhaus mit Stadel zu vermieten",
        price=1_800.0,
        price_type=PriceType.ASKING,
        outbuildings=["stadel"],
        town="Bad Aibling",
        postcode="83043",
    )
    db_session.add(PropertySource(property_id=old.id, source_id=source.id, url=_RENTAL_URL))
    db_session.flush()

    run = await runner_module.run_pipeline(source_keys=[_ADAPTER_KEY], trigger="test")
    assert run.status == "ok"
    db_session.refresh(old)
    assert old.price_type == PriceType.RENT
    assert _normalize_entry(run)["reasons"] == {}, "a known rental is not counted as dropped"

    on_radar = {p.id for p, _ in ranked_properties(db_session, SearchProfile())}
    assert old.id not in on_radar
