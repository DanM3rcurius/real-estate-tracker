"""The real run_pipeline() wiring must credit a pre-filtered-but-enumerated URL.

Review round 2, closing the gap round 1 flagged honestly: the round 1 fix
(``SourceAdapter.enumerated_urls`` + ``runner._mark_enumerated_as_seen``, see
``tests/integration/test_prefiltered_listing_survives_absence_detection.py``)
was pinned by calling ``_mark_enumerated_as_seen`` directly - which proves the
helper is correct, but not that ``run_pipeline()``'s crawl loop actually calls
it. The reviewer confirmed this gap is real: replacing the call site at
``runner.py`` with ``pass`` left the full suite green.

This file drives the real ``run_pipeline()`` end to end instead - with a
stubbed adapter (not the real DenkmalboerseAdapter, to keep the scenario
minimal and independent of any one source's own pre-filter logic) that
enumerates a URL it deliberately does not yield on its second run, exactly
the shape any pre-filtering adapter produces. Two ``run_pipeline()`` calls
share one session and one Source row (upserted by key, same as two real
weekly runs would): the first seeds a property by yielding it normally, the
second withholds it while still recording it as enumerated. If the crawl
loop's ``_mark_enumerated_as_seen`` call is ever removed, this test's final
assertion - the property must still be ACTIVE - fails.

``run_pipeline()`` is otherwise untested in isolation (no ``tests/pipeline/``
scaffolding exists), so ``load_config`` and ``session_scope`` are
monkeypatched here to point at this test's in-memory database and a
single-source config - the same scope-reduction test_phantom_removals.py
uses one level lower, applied one level higher this time because the
reviewer specifically asked for the real crawl path.
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
from hofradar.contracts import RawListing
from hofradar.db.enums import ListingStatus
from hofradar.db.models import Property, PropertySource
from hofradar.sources.base import SourceAdapter

_ADAPTER_KEY = "prefilter-stub"
_CONTROL_URL = "https://stub.invalid/objekt/control"
_TARGET_URL = "https://stub.invalid/objekt/target"


class _PrefilterStubAdapter(SourceAdapter):
    """Yields a control listing every run; yields the target only when told to.

    Always enumerates both URLs regardless - this is the exact shape
    DenkmalboerseAdapter's Bezirk/radius gates produce: a row examined, its
    URL recorded, but no RawListing yielded for it once a pre-filter decides
    against a fetch.
    """

    key = _ADAPTER_KEY
    #: Toggled by the test between its two run_pipeline() calls. A class
    #: attribute because get_adapter() builds a fresh instance each call.
    yield_target: bool = True

    async def discover(
        self, profile: SearchProfile, keywords: KeywordConfig
    ) -> AsyncIterator[RawListing]:
        self.begin_enumeration()
        self.record_enumerated_url(_CONTROL_URL)
        self.record_enumerated_url(_TARGET_URL)
        yield RawListing(
            source_key=self.key,
            url=_CONTROL_URL,
            title="Hofstelle in Bad Aibling",
            description="Hofstelle mit Stadel in Bad Aibling.",
            town="Bad Aibling",
            postcode="83043",
            price_raw="420.000 EUR",
        )
        if type(self).yield_target:
            yield RawListing(
                source_key=self.key,
                url=_TARGET_URL,
                title="Hofstelle in Miesbach",
                description="Hofstelle mit Nebengebaeude in Miesbach.",
                town="Miesbach",
                postcode="83714",
                price_raw="610.000 EUR",
            )
        # enumeration_complete stays True (begin_enumeration()'s default): a
        # genuinely complete walk either way, same as a real adapter's would
        # be when every row was examined and no detail fetch failed.

    async def fetch_detail(self, url: str) -> RawListing | None:  # pragma: no cover - unused
        raise NotImplementedError


def _adapter_config() -> SourceConfig:
    return SourceConfig(
        key=_ADAPTER_KEY,
        name="Pre-filter stub",
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


def _app_config() -> AppConfig:
    return AppConfig(
        profile=SearchProfile(),
        keywords=KeywordConfig(
            core=["Hofstelle"], buildings=[], hidden_phrases=[], regional=[], negative=[]
        ),
        sources=[_adapter_config()],
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
async def test_run_pipeline_does_not_remove_a_url_it_enumerated_but_did_not_yield(
    db_session, monkeypatch
) -> None:
    monkeypatch.setenv("HOFRADAR_OFFLINE", "1")
    monkeypatch.setattr(runner_module, "load_config", _app_config)
    monkeypatch.setattr(runner_module, "session_scope", lambda: _session_scope_over(db_session))
    monkeypatch.setitem(sources_module.ADAPTERS, _ADAPTER_KEY, _PrefilterStubAdapter)
    _PrefilterStubAdapter.yield_target = True

    # Run 1: both listings yielded normally, both become properties.
    run1 = await runner_module.run_pipeline(source_keys=[_ADAPTER_KEY], trigger="test")
    assert run1.status == "ok"

    target_ps = db_session.scalar(
        select(PropertySource).where(PropertySource.url == _TARGET_URL)
    )
    assert target_ps is not None, "the target must have been ingested on run 1"
    target_id = target_ps.property_id
    control_ps = db_session.scalar(
        select(PropertySource).where(PropertySource.url == _CONTROL_URL)
    )
    assert control_ps is not None, "the control must have been ingested on run 1"

    # Run 2: the target is enumerated (its URL recorded) but not yielded -
    # exactly what a pre-filter does. Only the control is yielded, keeping
    # the seen-set non-empty so mark_missing's guards do not mask the bug
    # (see tests/integration/test_phantom_removals.py for why those guards
    # exist).
    _PrefilterStubAdapter.yield_target = False
    run2 = await runner_module.run_pipeline(source_keys=[_ADAPTER_KEY], trigger="test")
    assert run2.status == "ok"

    target = db_session.get(Property, target_id)
    assert target is not None
    assert target.listing_status == ListingStatus.ACTIVE, (
        "the target's URL was enumerated (not yielded, but examined) on run 2 - "
        "the crawl loop must credit it as still seen, not read its absence "
        "from the yield stream as a removal"
    )
    assert target.removed_at is None
