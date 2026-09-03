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
finding to report, not something to paper over. It does not: entries come
through with a link that ``_entry_to_listing`` skips a missing one, and
their titles, ids, dates and body summaries all populate exactly where the
adapter reads them.
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
    assert first.source_date_raw == "2026-09-03T21:46:22Z"
    assert "Reihenmittelhaus" in first.description

    last = results[-1]
    assert last.url == "https://ovbimmo.de/immobilien/max-joseph-ue60-wohnen-am-park-GKCG94"
    assert last.title == "MAX JOSEPH Ü60-Wohnen am Park"
