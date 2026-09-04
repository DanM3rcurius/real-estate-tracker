"""A pasted exposé has to come out the other end visible.

Issue #3 reported a listing added through *Hinzufügen* that saved, got a
``public_id``, and then never appeared on the Radar. Every stage behaved
correctly given its inputs, which is why nothing errored: the address was on an
unlabelled line, so ``location_raw`` stayed ``None``, so there was nothing to
geocode, so there was no distance, so the confidence gate held the property off
the shortlist. ``appears on the radar: 0 of 1``.

Tracing one stage cannot catch that - the defect only exists as the composition
of five correct stages. So this walks the whole path with the real parser, the
real ``locate`` (Nominatim and OSRM mocked at the HTTP boundary), the real
``ingest`` and the real scorer, and asserts on the number the user cared about.
"""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from hofradar.config import KeywordConfig, SearchProfile
from hofradar.geo import locate
from hofradar.geo.geocoding import DEFAULT_NOMINATIM_URL
from hofradar.lifecycle import ingest
from hofradar.normalize import normalize_listing
from hofradar.scoring import ranked_properties, rescore_all
from hofradar.sources.adapters.manual import _from_plain_text

#: Exactly what the reporter pasted, unlabelled location line and all.
PASTED = """Sacherl mit Stadel in Alleinlage bei Vogtareuth
Kaufpreis: 595.000 EUR VB
Grundstück: 8.000 m2
Wohnfläche: 240 m2
Baujahr: 1891
83569 Vogtareuth, Landkreis Rosenheim

Ehemaliger Bauernhof. Scheune, Stall und Tenne. Obstgarten. Teilung moeglich.
Verkauf aus Altersgruenden, privat zu verkaufen, kein Makler."""

CENTER = (47.907, 11.840)
VOGTAREUTH = (47.9314, 12.1889)

_OSRM_PATTERN = re.compile(r"^https://router\.project-osrm\.org/route/v1/driving/.*")


def _nominatim_hit() -> list[dict]:
    return [
        {
            "lat": str(VOGTAREUTH[0]),
            "lon": str(VOGTAREUTH[1]),
            "display_name": "Vogtareuth, Landkreis Rosenheim, Bayern, Deutschland",
            "addresstype": "village",
            "class": "place",
            "type": "village",
        }
    ]


@pytest.mark.asyncio
async def test_a_pasted_sacherl_is_visible_on_the_radar(db_session, make_source) -> None:
    profile = SearchProfile(
        center={"name": "test-center", "lat": CENTER[0], "lon": CENTER[1]},
        radius={"air_km_max": 80},
    )

    raw = _from_plain_text("manual", "manual:test", PASTED, http_status=None)

    # Stage 1. The labelled fields were never the problem; the address was.
    assert raw.price_raw == "595.000 EUR VB"
    assert raw.location_raw is None

    listing = normalize_listing(raw, KeywordConfig())
    assert listing.postcode == "83569"
    assert listing.town == "Vogtareuth"

    with respx.mock:
        respx.get(DEFAULT_NOMINATIM_URL).mock(
            return_value=httpx.Response(200, json=_nominatim_hit())
        )
        respx.route(url__regex=_OSRM_PATTERN).mock(
            return_value=httpx.Response(
                200, json={"code": "Ok", "routes": [{"distance": 31000.0, "duration": 1700.0}]}
            )
        )
        geo = await locate(db_session, listing, profile)

    # Stage 2-3. Previously: lat=None lon=None precision='none' air=None.
    assert geo.precision != "none"
    assert geo.distance_air_km is not None
    assert geo.distance_driving_km is not None

    source = make_source(key="manual", role="local", reliability=0.8)
    prop, _ = ingest(db_session, listing, source=source, geo=geo, run_id=1)
    db_session.flush()

    rescore_all(db_session, profile, only_dirty=False)

    # Stage 4-5, and the number from the report: "appears on the radar: 0 of 1".
    ranked = ranked_properties(db_session, profile)
    assert [p.public_id for p, _ in ranked] == [prop.public_id]

    _, score = ranked[0]
    assert "SHORTLIST_BLOCKED" not in (score.flags or [])
    assert score.confidence_score >= profile.gates.min_confidence_for_shortlist


@pytest.mark.asyncio
async def test_a_paste_with_no_location_is_still_refused_the_shortlist(
    db_session, make_source
) -> None:
    """The gate is not the bug and must keep doing its job.

    A listing that genuinely cannot be placed is still un-geocodable, still
    below the gate, and still off the shortlist - but it now says so in
    ``warnings`` instead of looking like it worked.
    """
    raw = _from_plain_text(
        "manual", "manual:noloc", "Schönes Sacherl\nKaufpreis: 300.000 EUR", http_status=None
    )
    listing = normalize_listing(raw, KeywordConfig())

    assert listing.town is None
    assert any("no location found" in w for w in listing.warnings)
