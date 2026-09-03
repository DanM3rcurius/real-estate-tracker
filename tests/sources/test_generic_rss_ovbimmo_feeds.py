"""generic_rss against the ovbimmo.de Atom feeds configured in sources.yaml.

Two things are verified here, fully offline:

1. The four ovbimmo.de feed URLs added to ``generic_rss``'s
   ``options.feeds`` in ``config/sources.yaml`` load through the real config
   machinery (``hofradar.config.load_sources`` -> ``hofradar.sources.get_adapter``)
   and reach the adapter unchanged - the wiring the config edit depends on,
   and the one thing this task must not merely assert about the YAML file in
   isolation.
2. ``GenericRssAdapter.discover()`` actually parses this feed shape: a real
   ``suchergebnisse.atom`` capture from ``ovbimmo.de`` (Lkr. Rosenheim,
   ``a=de.rosenheim-kreis``), captured 2026-09-03, 100 entries, mixing plain
   Atom elements (``<title>``, ``<id>``, ``<link>``, ``<updated>``,
   ``<summary>``) with the ``classmarkets`` ``cm:``/``cms:`` namespaces this
   feed carries alongside them. See the provenance comment at the top of
   ``tests/fixtures/html/ovbimmo_suchergebnisse_rosenheim.atom``.

If ``feedparser`` ever choked on this feed shape (the two ``<link>``
elements per entry - one with no ``rel`` and one with an explicit
``rel="alternate"`` - or the classmarkets namespaces), that would be a real
finding to report, not something to paper over. It does not: every entry
that carries a ``<link>`` comes through as a ``RawListing`` (only the rare
entry with none would be skipped by ``_entry_to_listing``, and this capture
has none), and titles, ids, dates and body summaries all populate exactly
where the adapter reads them - as does ``media:thumbnail``, the one
image field this feed carries that is a standard Media RSS element rather
than a classmarkets vendor extension. The ``cm:price``/``cm:rooms``/
``cm:area``/``cm:locality`` fields stay unread here on purpose - see
docs/SOURCES.md for exactly what feedparser does and does not surface from
them, and why reading them belongs to the dedicated ``ovbimmo`` adapter, not
to this generic one.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from hofradar.config import load_sources
from hofradar.sources import get_adapter
from hofradar.sources.adapters.generic_rss import GenericRssAdapter

_REAL_ROSENHEIM_FEED_URL = (
    "https://ovbimmo.de/suchergebnisse.atom?t=all:sale:living&a=de.rosenheim-kreis"
)


def test_generic_rss_options_feeds_wired_from_real_config() -> None:
    """The ovbimmo.de Atom feeds configured in sources.yaml reach the adapter."""
    config = {s.key: s for s in load_sources()}
    adapter = get_adapter(config["generic_rss"])

    assert isinstance(adapter, GenericRssAdapter)
    feeds = adapter.options["feeds"]
    assert len(feeds) == 4
    assert all(url.startswith("https://ovbimmo.de/suchergebnisse.atom") for url in feeds)
    assert any("a=de.rosenheim-kreis" in url for url in feeds)
    assert any("a=de.traunstein" in url for url in feeds)
    assert any("a=de.miesbach" in url for url in feeds)
    assert any("a=de.ebersberg" in url for url in feeds)
    # de.muehldorf-am-inn renders zero entries live - the wrong area id, never shipped.
    assert not any("muehldorf" in url for url in feeds)


@pytest.mark.asyncio
async def test_discover_parses_a_real_ovbimmo_atom_capture(
    make_source_config, search_profile, sample_keywords, read_fixture
):
    feed_xml = read_fixture("ovbimmo_suchergebnisse_rosenheim.atom")
    cfg = make_source_config(
        key="generic_rss",
        adapter="generic_rss",
        options={"feeds": [_REAL_ROSENHEIM_FEED_URL]},
    )
    adapter = GenericRssAdapter(cfg)

    with respx.mock:
        respx.get(_REAL_ROSENHEIM_FEED_URL).mock(
            return_value=httpx.Response(
                200, text=feed_xml, headers={"Content-Type": "application/atom+xml"}
            )
        )
        results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert len(results) == 100, "every entry in the real capture carries a usable <link>"

    first = results[0]
    assert first.source_key == "generic_rss"
    assert first.url == (
        "https://ovbimmo.de/immobilien/"
        "bungalow-reihenhaus-mit-terrasse-und-geschuetztem-gartenbereich-in-stephanskirchen-H23XY8"
    )
    assert first.title == (
        "Bungalow-Reihenhaus mit Terrasse und geschütztem Gartenbereich in Stephanskirchen"
    )
    # this feed's <id> IS the canonical listing URL, unlike a synthetic guid.
    assert first.external_id == first.url
    # media:thumbnail is standard Media RSS, not a classmarkets vendor
    # element - _entry_image_urls must read it, or every listing arrives
    # with no image at all.
    assert first.image_urls == [
        "https://cmcdn.de/img-service/gZC35Wg4MG_oSvykbmlwf6A_7bzRvyMO41JWtVMGTUsSFkArYkHRg5STyyJcZ9WiBdT"
        "-qDY7wquFuyIO9pE2z0Q;safescale=440x330,crop=440x330"
    ]
    assert first.source_date_raw == "2026-09-03T21:46:22Z"
    assert "Reihenmittelhaus" in first.description

    last = results[-1]
    assert last.url == "https://ovbimmo.de/immobilien/max-joseph-ue60-wohnen-am-park-GKCG94"
    assert last.title == "MAX JOSEPH Ü60-Wohnen am Park"


@pytest.mark.asyncio
async def test_configured_entry_field_map_lifts_the_feeds_own_location_fields(
    make_source_config, search_profile, sample_keywords, read_fixture
):
    """The location this route was blind to, driven by configuration.

    These entries state the postcode and the town only in classmarkets' own
    ``cm:`` namespace. Without them every property found through this route has
    ``town = NULL``, no geocode query and therefore no ``distance_air_km`` -
    so the report's "davon im Radius" column reads 0 for generic_rss forever
    and the per-Gemeinde coverage map prints Rosenheim and its neighbours as
    dark every week while OVB is actively producing there.

    The element names come from ``options.entry_field_map``, not from this
    adapter: ``GenericRssAdapter`` must stay generic, and hardcoding ``cm_``
    here would make it a classmarkets adapter under a generic name.
    """
    feed_xml = read_fixture("ovbimmo_suchergebnisse_rosenheim.atom")
    cfg = make_source_config(
        key="generic_rss",
        adapter="generic_rss",
        options={
            "feeds": [_REAL_ROSENHEIM_FEED_URL],
            "entry_field_map": {"cm_postalcode": "postcode", "cm_locality": "town"},
        },
    )
    adapter = GenericRssAdapter(cfg)

    with respx.mock:
        respx.get(_REAL_ROSENHEIM_FEED_URL).mock(
            return_value=httpx.Response(
                200, text=feed_xml, headers={"Content-Type": "application/atom+xml"}
            )
        )
        results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    first = results[0]
    assert first.postcode == "83071"
    assert first.town == "Stephanskirchen"
    # Not one or two lucky entries: this is the whole capture.
    located = [r for r in results if r.town and r.postcode]
    assert len(located) == 100
    # Raw strings only. cm:price and cm:area carry their value as element text
    # beside an attribute, which feedparser reduces to the attributes alone -
    # so there is nothing there to accidentally turn into a number, and the
    # mapping refuses non-string values anyway.
    assert all(isinstance(r.town, str) and isinstance(r.postcode, str) for r in located)
    assert all(r.price_raw is None for r in results)


@pytest.mark.asyncio
async def test_the_shipped_config_actually_carries_that_mapping(
    search_profile, sample_keywords, read_fixture
):
    """The mapping is only useful if the registry entry ships it - this reads
    the real config/sources.yaml rather than a test-local options dict."""
    config = {s.key: s for s in load_sources()}
    adapter = get_adapter(config["generic_rss"])

    assert adapter.options["entry_field_map"] == {
        "cm_postalcode": "postcode",
        "cm_locality": "town",
    }


@pytest.mark.asyncio
async def test_an_unmappable_target_field_is_ignored_not_written(
    make_source_config, search_profile, sample_keywords, read_fixture
):
    """Configuration may say where a raw string lives, never reshape what a
    listing is: identity and authority fields are not mappable."""
    feed_xml = read_fixture("ovbimmo_suchergebnisse_rosenheim.atom")
    cfg = make_source_config(
        key="generic_rss",
        adapter="generic_rss",
        options={
            "feeds": [_REAL_ROSENHEIM_FEED_URL],
            "entry_field_map": {"cm_locality": "contact_kind", "cm_postalcode": "postcode"},
        },
    )
    adapter = GenericRssAdapter(cfg)

    with respx.mock:
        respx.get(_REAL_ROSENHEIM_FEED_URL).mock(
            return_value=httpx.Response(
                200, text=feed_xml, headers={"Content-Type": "application/atom+xml"}
            )
        )
        results = [item async for item in adapter.discover(search_profile, sample_keywords)]

    assert all(r.contact_kind is None for r in results)
    assert results[0].postcode == "83071"
