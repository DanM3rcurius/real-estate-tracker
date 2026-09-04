"""What the crawl loop throws away has to be counted, not quietly dropped.

Issue #10 again, one layer up from the refusal itself. ``run_pipeline``'s
crawl loop had two bare ``continue`` statements (an excluded type, a listing
outside the radius) and no counter anywhere, so a source whose every row was
discarded produced exactly the same run summary as a source that found
nothing at all: ``listings_seen`` counted the fetches and nothing recorded
the verdicts. The refusal added for issue #10 is a third such path, and
``/runs`` renders ``run.log`` entries verbatim - so a ``rejected=N`` entry
with its reason breakdown is what makes all three visible.

The harness is the one ``test_run_pipeline_credits_prefiltered_urls.py``
established: the real ``run_pipeline`` over a stub adapter, with
``load_config`` and ``session_scope`` pointed at this test's in-memory
database.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import contextmanager
from datetime import date

import pytest
from sqlalchemy import select

import hofradar.pipeline.runner as runner_module
import hofradar.sources as sources_module
from hofradar.config import AppConfig, KeywordConfig, SearchProfile, SourceConfig
from hofradar.contracts import PAGE_KIND_INDEX, RawListing
from hofradar.db.enums import RunStage
from hofradar.db.models import Property
from hofradar.pipeline.runner import REJECT_EXCLUSION_FLAGS, REJECT_NOT_A_LISTING
from hofradar.sources.base import SourceAdapter

_ADAPTER_KEY = "reject-stub"
_LISTING_URL = "https://stub.invalid/objekt/hofstelle"
_INDEX_URL = "https://stub.invalid/kaufen/rosenheim-kreis"
_EXCLUDED_URL = "https://stub.invalid/objekt/wohnung"


class _RejectStubAdapter(SourceAdapter):
    """One real listing, one portal index page, one excluded type."""

    key = _ADAPTER_KEY

    async def discover(
        self, profile: SearchProfile, keywords: KeywordConfig
    ) -> AsyncIterator[RawListing]:
        self.begin_enumeration()
        yield RawListing(
            source_key=self.key,
            url=_LISTING_URL,
            title="Hofstelle in Bad Aibling",
            description="Hofstelle mit Stadel in Bad Aibling.",
            town="Bad Aibling",
            postcode="83043",
            price_raw="420.000 EUR",
        )
        yield RawListing(
            source_key=self.key,
            url=_INDEX_URL,
            title="Immobilien in Rosenheim (Kreis) kaufen",
            # The chimera from the issue: facts scraped off three different
            # result cards, which is why a fact-count gate cannot save us.
            description="Bauernhaus 461 m² Grundstück 434 m² Wohnfläche 83071 Stephanskirchen",
            town="Stephanskirchen",
            postcode="83071",
            price_raw="595.000 EUR",
            page_kind=PAGE_KIND_INDEX,
        )
        yield RawListing(
            source_key=self.key,
            url=_EXCLUDED_URL,
            title="Eigentumswohnung in Bad Aibling",
            description="Moderne Eigentumswohnung, Erstbezug.",
            town="Bad Aibling",
            postcode="83043",
            price_raw="310.000 EUR",
        )

    async def fetch_detail(self, url: str) -> RawListing | None:  # pragma: no cover - unused
        raise NotImplementedError


def _app_config() -> AppConfig:
    return AppConfig(
        profile=SearchProfile(),
        keywords=KeywordConfig(
            core=["Hofstelle"],
            buildings=["Stadel"],
            hidden_phrases=[],
            regional=[],
            negative=["Eigentumswohnung"],
        ),
        sources=[
            SourceConfig(
                key=_ADAPTER_KEY,
                name="Reject stub",
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


@pytest.mark.asyncio
async def test_a_refused_page_never_becomes_a_property_and_is_counted(
    db_session, monkeypatch
) -> None:
    monkeypatch.setenv("HOFRADAR_OFFLINE", "1")
    monkeypatch.setattr(runner_module, "load_config", _app_config)
    monkeypatch.setattr(runner_module, "session_scope", lambda: _session_scope_over(db_session))
    monkeypatch.setitem(sources_module.ADAPTERS, _ADAPTER_KEY, _RejectStubAdapter)

    run = await runner_module.run_pipeline(source_keys=[_ADAPTER_KEY], trigger="test")

    assert run.status == "ok"
    titles = set(db_session.scalars(select(Property.canonical_title)))
    assert titles == {"Hofstelle in Bad Aibling"}, (
        "the portal index page and the excluded type must not become properties"
    )

    entries = [e for e in run.log if e["stage"] == str(RunStage.NORMALIZE)]
    assert entries, "the run must say what it threw away"
    entry = entries[-1]
    assert entry["rejected"] == 2
    assert entry["reasons"] == {
        f"{REJECT_NOT_A_LISTING}:{PAGE_KIND_INDEX}": 1,
        REJECT_EXCLUSION_FLAGS: 1,
    }
