"""A pre-filtered row must not become a false REMOVED. Review round 1, Critical 1.

Making enumeration completeness conditional (this branch's own change) instead
of the old unconditional ``mark_enumeration_incomplete`` activated a bug the
old code made inert. ``DenkmalboerseAdapter``'s Bezirk and gazetteer
pre-filters (see ``hofradar.sources.adapters.denkmalboerse``) skip a detail
fetch - and so never yield a ``RawListing`` for that URL - before
``hofradar.pipeline.runner`` ever gets a chance to run its "record this URL
as still seen" step, which is deliberately placed *before* any filter so a
listing the pipeline chooses not to ingest is not mistaken for one the source
stopped offering (see the comment at ``runner.py`` around line 110). A
pre-filtered-but-still-live listing was therefore indistinguishable from a
genuinely withdrawn one: narrow ``air_km_max``, or narrow
``options.regierungsbezirke``, and the next run's ``mark_missing`` would
write REMOVED into append-only history for a property still on the site.

The fix is ``SourceAdapter.enumerated_urls`` (every URL discover() examined
this run, fetched or not) plus ``hofradar.pipeline.runner._mark_enumerated_as_seen``
(credits any such URL a property is already on record under as "seen", same
as a yielded listing). This file traces that path end to end with the real
adapter and the real ``mark_missing``, at the same level below
``run_pipeline`` that ``test_phantom_removals.py`` already uses for the
sibling bug (GitHub issue #2) - ``run_pipeline`` itself has no test
scaffolding to invoke it in isolation (LLM review, scoring and reporting all
need to be stood up too), so this reuses the private crawl-loop helpers
runner.py itself calls rather than reimplementing their logic.
"""

from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx

from hofradar.config import KeywordConfig, RadiusConfig, SearchProfile, SourceConfig
from hofradar.db.enums import ListingStatus, SourceRole
from hofradar.lifecycle import ingest, mark_missing
from hofradar.pipeline.runner import _known_property_id, _mark_enumerated_as_seen
from hofradar.sources import get_adapter

BASE = "https://www.blfd.bayern.de"
INDEX = f"{BASE}/cgi-bin/fts_search_verkauf.pl"
KIRCHSEEON_URL = f"{BASE}/information-service/denkmalboerse/objekte/008616/index.html"
#: Real object, real address - 63.4km from the project's default profile
#: origin by hofradar.geo.distance.haversine_km. Inside an 80km radius,
#: outside a 60km one.
MUEHLDORF_URL = f"{BASE}/information-service/denkmalboerse/objekte/007505/index.html"
DETAIL_URL_RE = rf"{BASE}/information-service/denkmalboerse/objekte/\d{{6}}/index\.html"

#: Two real Denkmalbörse rows, both Oberbayern (so neither is affected by the
#: Bezirk gate here - this file is about the radius gate narrowing, the exact
#: scenario the reviewer traced).
_SEARCH_HTML = """
    <table><tbody>
    <tr>
      <td>price</td>
      <td><a href="/information-service/denkmalboerse/objekte/008616/index.html">
        Bauernhof in Kirchseeon</a><p>85614 Kirchseeon</p></td>
      <td>Oberbayern</td>
    </tr>
    <tr>
      <td>price</td>
      <td><a href="/information-service/denkmalboerse/objekte/007505/index.html">
        Stadthaus in Muehldorf am Inn</a><p>84453 Muehldorf am Inn</p></td>
      <td>Oberbayern</td>
    </tr>
    </tbody></table>
"""


def _adapter_config() -> SourceConfig:
    """Self-contained: this directory has no ``make_source_config`` fixture
    (that lives in tests/sources/conftest.py), so build the SourceConfig
    directly rather than pulling that fixture cross-package.
    """
    return SourceConfig(
        key="denkmalboerse",
        name="BLfD Denkmalbörse",
        role="primary",
        adapter="denkmalboerse",
        base_url=BASE,
        reliability=0.8,
        enabled=True,
        rate_limit_seconds=0.0,
        respect_robots=False,
        terms_checked_at=date(2026, 9, 3),
        terms_excerpt="Test fixture source - not the real terms check.",
        options={},
    )


_KEYWORDS = KeywordConfig(
    core=["Hofstelle"], buildings=[], hidden_phrases=[], regional=[], negative=[]
)


def _seed(db_session, make_source, make_listing):
    """Two properties already on record, as if a wide-radius run ingested them.

    Deliberately distinct town/postcode/price/land, same discipline
    test_phantom_removals.py's ``_seed`` uses: a fingerprint collision here
    would dedupe the two into one property and test nothing about absence
    detection.
    """
    source = make_source(key="denkmalboerse", role=SourceRole.PRIMARY)
    kirchseeon = make_listing(
        url=KIRCHSEEON_URL,
        title="Bauernhof in Kirchseeon",
        town="Kirchseeon",
        postcode="85614",
        price=420_000.0,
        land_sqm=1_800.0,
    )
    muehldorf = make_listing(
        url=MUEHLDORF_URL,
        title="Stadthaus in Muehldorf am Inn",
        town="Muehldorf am Inn",
        postcode="84453",
        price=610_000.0,
        land_sqm=900.0,
    )
    kirchseeon_prop, _ = ingest(db_session, kirchseeon, source=source, run_id=1)
    muehldorf_prop, _ = ingest(db_session, muehldorf, source=source, run_id=1)
    db_session.flush()
    assert kirchseeon_prop.id != muehldorf_prop.id, "fixture listings must not deduplicate"
    return source, kirchseeon_prop, muehldorf_prop


async def _discover_seen_ids(db_session, source, adapter, profile, keywords) -> set[int]:
    """The runner's "record seen before any filter" step, for yielded listings only.

    Exactly runner.py's inline logic (see the comment there) - reused, not
    reimplemented, so this test exercises the real helper.
    """
    seen: set[int] = set()
    async for raw in adapter.discover(profile, keywords):
        known_id = _known_property_id(db_session, source.id, raw.url)
        if known_id is not None:
            seen.add(known_id)
    return seen


@pytest.mark.asyncio
async def test_narrowing_the_radius_does_not_falsely_remove_a_prefiltered_listing(
    db_session, make_source, make_listing
) -> None:
    """The reported scenario: an operator drags air_km_max from 80 down to
    60. Muehldorf am Inn (63.4km) falls outside the gazetteer gate and is
    never re-fetched - but it was examined, and enumerated_urls plus
    _mark_enumerated_as_seen must credit it as seen anyway.
    """
    source, kirchseeon_prop, muehldorf_prop = _seed(db_session, make_source, make_listing)
    narrow_profile = SearchProfile(radius=RadiusConfig(air_km_max=60))
    adapter = get_adapter(_adapter_config())

    with respx.mock:
        respx.get(INDEX).mock(return_value=httpx.Response(200, text=_SEARCH_HTML))
        respx.route(url__regex=DETAIL_URL_RE).mock(
            return_value=httpx.Response(200, text="<html><body>Objekt</body></html>")
        )
        seen = await _discover_seen_ids(db_session, source, adapter, narrow_profile, _KEYWORDS)
        # The fix under test: credit every examined-but-skipped URL too.
        _mark_enumerated_as_seen(db_session, source, adapter, seen)

    assert adapter.enumeration_complete is True, "the walk at 60km still finished cleanly"
    assert MUEHLDORF_URL in adapter.enumerated_urls, "the row was examined, just not re-fetched"
    assert kirchseeon_prop.id in seen, "Kirchseeon (20.6km) is still in radius and was yielded"
    assert muehldorf_prop.id in seen, "pre-filtered but examined - must still count as seen"

    changes = mark_missing(
        db_session, seen, source=source, run_id=2, enumeration_complete=adapter.can_prove_absence
    )

    assert changes == [], "nothing was actually disproven - nothing may be marked removed"
    assert muehldorf_prop.listing_status == ListingStatus.ACTIVE
    assert muehldorf_prop.removed_at is None


@pytest.mark.asyncio
async def test_without_crediting_enumerated_urls_the_bug_reproduces(
    db_session, make_source, make_listing
) -> None:
    """What this file's sibling test's fix closes: build the seen-set the old
    way - only from what discover() actually yielded, skipping
    _mark_enumerated_as_seen - and mark_missing writes a false REMOVED for a
    listing that is still live and simply was not re-fetched this run. This
    is the regression pin: if the runner ever stops calling
    _mark_enumerated_as_seen, the sibling test above would start failing for
    the wrong reason (it calls the helper explicitly) - this test is what
    proves the helper is actually necessary in the first place.
    """
    source, _kirchseeon_prop, muehldorf_prop = _seed(db_session, make_source, make_listing)
    narrow_profile = SearchProfile(radius=RadiusConfig(air_km_max=60))
    adapter = get_adapter(_adapter_config())

    with respx.mock:
        respx.get(INDEX).mock(return_value=httpx.Response(200, text=_SEARCH_HTML))
        respx.route(url__regex=DETAIL_URL_RE).mock(
            return_value=httpx.Response(200, text="<html><body>Objekt</body></html>")
        )
        seen = await _discover_seen_ids(db_session, source, adapter, narrow_profile, _KEYWORDS)
        # Deliberately NOT calling _mark_enumerated_as_seen - this is the bug.

    changes = mark_missing(
        db_session, seen, source=source, run_id=2, enumeration_complete=adapter.can_prove_absence
    )

    assert len(changes) == 1
    assert muehldorf_prop.listing_status == ListingStatus.REMOVED
    assert muehldorf_prop.removed_at is not None
