"""An OVB listing has to arrive with a town, or the report cannot see it.

Measured on this branch before the fix: 0 of 100 Atom entries and the real
detail capture yielded a town, a postcode or any geocode query at all -
``hofradar.geo.locate._build_geocode_queries`` returned ``[]``. So
``Property.town`` and ``Property.distance_air_km`` were NULL for the entire
OVB inventory, and both of the report's new instruments went blind on it:

* ``source_yield``'s "davon im Radius" column reads 0 forever for a source
  whose properties have no distance - the go/no-go number for the two sources
  shipped beside it, unable to measure either of them;
* ``coverage_by_municipality`` matches ``Property.town`` exactly, so every
  expected Gemeinde printed as dark every week - under German copy asserting
  that a Gemeinde without hits is *uncovered* rather than quiet. Rosenheim,
  Bad Aibling and Bruckmühl were being reported as uncovered while OVB was
  actively producing there.

This traces the whole path with the real capture and no network: the adapter's
own ``fetch_detail``, the real normalizer, the real ``locate`` (Nominatim and
OSRM mocked at the HTTP boundary), the real ``ingest``, and then the two report
queries that were blind.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from hofradar.config import KeywordConfig, SearchProfile, SourceConfig
from hofradar.geo import locate
from hofradar.geo.geocoding import DEFAULT_NOMINATIM_URL
from hofradar.geo.locate import _build_geocode_queries
from hofradar.lifecycle import ingest
from hofradar.normalize import normalize_listing
from hofradar.report.yield_stats import coverage_by_municipality, source_yield
from hofradar.sources import get_adapter

BASE = "https://ovbimmo.de"
DETAIL_URL = (
    f"{BASE}/immobilien/zweifamilienhaus-grosskarolinenfeld-"
    "grosser-sonniger-garten-H3N33B"
)
#: The project's own default search origin (config/search.yaml).
CENTER = (47.907, 11.840)
#: Großkarolinenfeld, the town the captured listing is actually in.
GROSSKAROLINENFELD = (47.8836, 12.0787)

_OSRM_PATTERN = re.compile(r"^https://router\.project-osrm\.org/route/v1/driving/.*")

#: This directory has no ``read_fixture``/``make_source_config`` fixture (both
#: live in tests/sources/conftest.py), so both are inlined rather than pulled
#: cross-package - the same choice
#: test_prefiltered_listing_survives_absence_detection.py makes.
_FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "html"


def _adapter_config() -> SourceConfig:
    return SourceConfig(
        key="ovbimmo",
        name="OVBimmo (OVB Heimatzeitungen)",
        role="local",
        adapter="ovbimmo",
        base_url=BASE,
        reliability=0.75,
        enabled=True,
        rate_limit_seconds=0.0,
        respect_robots=False,
        listing_ttl_days=14,
        terms_checked_at=date(2026, 9, 3),
        terms_excerpt="Test fixture source - not the real terms check.",
        options={"municipalities": ["rosenheim-kreis"], "property_types": ["haus"]},
    )


def _nominatim_hit(lat: float, lon: float) -> list[dict]:
    return [
        {
            "lat": str(lat),
            "lon": str(lon),
            "display_name": "Großkarolinenfeld, Landkreis Rosenheim, Bayern, Deutschland",
            "addresstype": "town",
            "class": "place",
            "type": "town",
        }
    ]


@pytest.mark.asyncio
async def test_an_ovb_listing_reaches_the_report_with_a_town_and_a_distance(
    db_session, make_source
) -> None:
    adapter = get_adapter(_adapter_config())
    profile = SearchProfile(
        center={"name": "test-center", "lat": CENTER[0], "lon": CENTER[1]},
        radius={"air_km_max": 80},
    )

    with respx.mock:
        respx.get(DETAIL_URL).mock(
            return_value=httpx.Response(
                200,
                text=(_FIXTURES_DIR / "ovbimmo_detail.html").read_text(encoding="utf-8"),
            )
        )
        raw = await adapter.fetch_detail(DETAIL_URL)

    assert raw is not None
    listing = normalize_listing(raw, KeywordConfig())

    # The measurement the reviewer took, now non-empty. An empty list here is
    # the whole defect: no query means no geocode, no geocode means no
    # distance, and no distance means the yield column can never count it.
    assert _build_geocode_queries(listing) == [
        "83109 Großkarolinenfeld",
        "Großkarolinenfeld",
        "83109",
    ]

    with respx.mock:
        respx.get(DEFAULT_NOMINATIM_URL).mock(
            return_value=httpx.Response(200, json=_nominatim_hit(*GROSSKAROLINENFELD))
        )
        respx.route(url__regex=_OSRM_PATTERN).mock(
            return_value=httpx.Response(
                200, json={"code": "Ok", "routes": [{"distance": 25000.0, "duration": 1500.0}]}
            )
        )
        geo = await locate(db_session, listing, profile)

    assert geo.distance_air_km is not None

    source = make_source(key="ovbimmo", role="local", reliability=0.75)
    prop, _ = ingest(db_session, listing, source=source, geo=geo, run_id=1)

    assert prop.town == "Großkarolinenfeld"
    assert prop.postcode == "83109"
    assert prop.distance_air_km == pytest.approx(geo.distance_air_km)

    since = datetime.now(UTC) - timedelta(days=7)

    # The yield instrument can now see this source at all.
    yields = {row.source_key: row for row in source_yield(
        session=db_session, since=since, radius_air_km=profile.radius.air_km_max
    )}
    assert yields["ovbimmo"].observed == 1
    assert yields["ovbimmo"].in_radius == 1

    # ...and so can the coverage map, which matches Property.town exactly.
    coverage = {
        row.town: row.observed
        for row in coverage_by_municipality(
            db_session, since=since, expected=["Großkarolinenfeld", "Weyarn"]
        )
    }
    assert coverage["Großkarolinenfeld"] == 1
    # The other half of the instrument still works: a town with nothing in it
    # is still reported as a named silence.
    assert coverage["Weyarn"] == 0
