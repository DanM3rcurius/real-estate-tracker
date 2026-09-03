"""The static snapshot published to GitHub Pages.

These tests exist because the export is the one artefact nobody looks at before
it goes public. What they pin is not how a page looks - the app's own tests do
that - but the four ways a static copy can be wrong in a way the server version
never is: a link that points outside the subdirectory, a control that posts to a
server that is not there, a missing banner, and a fact that changes meaning
because it was rendered without its context.
"""

from __future__ import annotations

import html
import json
import re
from pathlib import Path

import pytest

from hofradar.web.export import (
    ExportError,
    ExportResult,
    export_site,
    inject_base_global,
    rewrite_urls,
)

BASE = "/real-estate-tracker"

#: Any root-absolute URL that did not pick up the base prefix.
UNPREFIXED = re.compile(rf'(?:href|src|action)="/(?!{BASE.lstrip("/")}/)[^"]*"')


@pytest.fixture()
def exported(tmp_path: Path, session_factory, seeded) -> ExportResult:
    return export_site(tmp_path / "site", base_path=BASE, session_factory=session_factory)


def _html_files(root: Path) -> list[Path]:
    return sorted(root.rglob("*.html"))


# --------------------------------------------------------------------------- #
# The URL rewriting, on its own
# --------------------------------------------------------------------------- #


def test_rewrite_prefixes_root_absolute_urls():
    markup = '<a href="/map">k</a><img src="/static/x.png"><form action="/report">'
    out = rewrite_urls(markup, BASE)

    assert 'href="/real-estate-tracker/map"' in out
    assert 'src="/real-estate-tracker/static/x.png"' in out
    assert 'action="/real-estate-tracker/report"' in out


def test_rewrite_leaves_external_and_protocol_relative_urls_alone():
    """An absolute URL is already complete; prefixing it would break it."""
    markup = (
        '<a href="https://example.invalid/listing">q</a>'
        '<a href="//tile.openstreetmap.org/1.png">t</a>'
        '<a href="relative/page">r</a>'
    )
    assert rewrite_urls(markup, BASE) == markup


def test_rewrite_is_a_no_op_without_a_base_path():
    markup = '<a href="/map">k</a>'
    assert rewrite_urls(markup, "") == markup


def test_base_global_is_injected_into_the_head():
    out = inject_base_global("<html><head><title>x</title></head></html>", BASE)
    assert '<script>window.HOFRADAR_BASE="/real-estate-tracker";</script>' in out
    assert out.index("HOFRADAR_BASE") < out.index("</head>")


# --------------------------------------------------------------------------- #
# The export as a whole
# --------------------------------------------------------------------------- #


def test_export_writes_the_expected_pages(exported):
    root = exported.destination

    for page in ("index.html", "map/index.html", "report/index.html", "runs/index.html"):
        assert (root / page).is_file(), page
    assert (root / "api/properties.json").is_file()
    assert (root / "api/export.csv").is_file()
    # Pages runs Jekyll unless told not to, which would drop _-prefixed paths.
    assert (root / ".nojekyll").is_file()


def test_every_property_gets_a_dossier(exported, seeded):
    for prop in seeded.values():
        page = exported.destination / "property" / prop.public_id / "index.html"
        assert page.is_file(), prop.public_id
    assert exported.properties == len(seeded)


def test_vendored_assets_are_copied(exported):
    """Nothing may be pulled from a CDN at view time - DECISIONS.md 7."""
    root = exported.destination
    assert (root / "static/app.js").is_file()
    assert (root / "static/app.css").is_file()
    assert (root / "static/vendor/htmx.min.js").is_file()
    assert (root / "static/vendor/leaflet/leaflet.js").is_file()
    assert (root / "static/vendor/leaflet/images/marker-icon.png").is_file()


def test_no_url_escapes_the_base_path(exported):
    """The failure this catches is a site where every link 404s."""
    offenders = {
        str(page.relative_to(exported.destination)): UNPREFIXED.findall(
            page.read_text(encoding="utf-8")
        )
        for page in _html_files(exported.destination)
    }
    assert not {k: v for k, v in offenders.items() if v}


def test_nothing_posts_to_a_server_that_is_not_there(exported):
    for page in _html_files(exported.destination):
        markup = page.read_text(encoding="utf-8")
        assert "hx-post" not in markup, page
        assert "hx-get" not in markup, page
        assert 'method="post"' not in markup, page


def test_every_page_carries_the_snapshot_banner(exported):
    """A public page of invented listings must say so, on every page."""
    for page in _html_files(exported.destination):
        markup = page.read_text(encoding="utf-8")
        assert "Statischer Schnappschuss" in markup, page


def test_write_only_pages_are_dropped_from_the_navigation(exported):
    index = (exported.destination / "index.html").read_text(encoding="utf-8")
    assert f'href="{BASE}/map"' in index
    assert f'href="{BASE}/add"' not in index
    assert f'href="{BASE}/settings"' not in index


def test_the_dossier_says_triage_is_unavailable_rather_than_offering_it(exported, seeded):
    page = exported.destination / "property" / seeded["near"].public_id / "index.html"
    markup = page.read_text(encoding="utf-8")

    assert "nicht verfügbar" in markup
    assert 'id="triage"' not in markup


def test_an_unmeasured_road_distance_is_still_unmeasured_on_the_map(exported, seeded):
    """Invariant 3 has to survive the trip through the exporter."""
    markup = (exported.destination / "map/index.html").read_text(encoding="utf-8")
    match = re.search(r"data-points='([^']*)'", markup)
    assert match, "the map lost its point payload"

    points = json.loads(html.unescape(match.group(1)))
    by_id = {p["public_id"]: p for p in points}

    # `near` was seeded with distance_driving_km None and must stay that way.
    near = by_id[seeded["near"].public_id]
    assert near["distance_driving_km"] is None
    assert near["distance_driving_checked"] is False
    assert near["distance_air_km"] is not None


def test_the_sliders_survive_so_the_page_still_demonstrates_the_product(exported):
    """The derived bands are computed client-side; only the list is frozen."""
    index = (exported.destination / "index.html").read_text(encoding="utf-8")

    assert 'id="air_km_max"' in index
    assert 'id="total_budget_max"' in index
    # app.js reads these to recompute the bands without a server.
    assert "data-driving-soft=" in index
    assert "data-purchase-share=" in index


def test_export_without_a_base_path_still_works(tmp_path, session_factory, seeded):
    """A user-or-organisation Pages site is served from the root."""
    result = export_site(tmp_path / "root-site", session_factory=session_factory)
    index = (result.destination / "index.html").read_text(encoding="utf-8")

    assert 'href="/map"' in index
    assert '<script>window.HOFRADAR_BASE="";</script>' in index


def test_a_broken_page_stops_the_export(tmp_path, session_factory, seeded, monkeypatch):
    """Publishing a snapshot with a 500 in it is worse than not publishing."""
    import hofradar.web.export as export_module

    monkeypatch.setattr(
        export_module,
        "STATIC_ROUTES",
        (*export_module.STATIC_ROUTES, ("/does-not-exist", "nope/index.html")),
    )

    with pytest.raises(ExportError, match="404"):
        export_site(tmp_path / "broken", session_factory=session_factory)
