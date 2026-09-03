"""The two sliders are the product, so they get the most tests.

Three properties are asserted here:
  1. moving a slider changes ``profile_hash`` - the system really is asking a
     different question, not just filtering the same answer;
  2. a narrower slider shows strictly less;
  3. garbage in the query string clamps, and never produces a 500. The value
     comes from a URL a human can edit, so it must be treated as hostile.
"""

from __future__ import annotations

import re

import pytest

from hofradar.web.deps import (
    AIR_KM_MAX,
    AIR_KM_MIN,
    BUDGET_MAX,
    BUDGET_MIN,
    profile_from_query,
)

HASH_RE = re.compile(r"Profil ([0-9a-f]{24})")


def hashes(html: str) -> str:
    match = HASH_RE.search(html)
    assert match, f"no profile hash in response: {html[:300]}"
    return match.group(1)


def card_ids(html: str) -> set[str]:
    return set(re.findall(r'href="/property/([A-Za-z0-9\-]+)"', html))


def test_moving_sliders_changes_profile_hash(client, seeded):
    default_html = client.get("/api/results").text
    narrow_html = client.get("/api/results?air_km_max=40&total_budget_max=800000").text
    assert hashes(default_html) != hashes(narrow_html)


def test_narrower_radius_removes_the_far_property(client, seeded):
    wide = client.get("/api/results?air_km_max=200&total_budget_max=3000000").text
    narrow = client.get("/api/results?air_km_max=40&total_budget_max=3000000").text

    assert "HF-0002" in card_ids(wide)
    assert "HF-0002" not in card_ids(narrow)
    assert "HF-0001" in card_ids(narrow)
    assert card_ids(narrow) < card_ids(wide)


def test_narrower_budget_removes_the_expensive_property(client, seeded):
    # 800k total budget -> purchase target 500k, hard band 600k: the 850k farm goes.
    wide = client.get("/api/results?air_km_max=200&total_budget_max=3000000").text
    narrow = client.get("/api/results?air_km_max=200&total_budget_max=800000").text
    assert "HF-0004" in card_ids(wide)
    assert "HF-0004" not in card_ids(narrow)
    assert "HF-0002" in card_ids(narrow)  # far, but cheap: the radius is still wide


def test_status_line_shows_hash_and_rescore_count(client, seeded):
    html = client.get("/api/results").text
    assert "Profil " in html
    assert "im Filter" in html


@pytest.mark.parametrize(
    "query",
    [
        "air_km_max=banana",
        "air_km_max=",
        "air_km_max=-5000",
        "air_km_max=999999999",
        "air_km_max=NaN",
        "air_km_max=1e400",
        "total_budget_max=viel%20Geld",
        "total_budget_max=0",
        "total_budget_max=-1",
        "total_budget_max=1e400",
        "min_land_sqm=%3Cscript%3E",
        "sort=;DROP+TABLE",
        "status=nonsense",
        "limit=abc",
        "limit=-4",
        "include_rejected=vielleicht",
        "air_km_max=40&air_km_max=80",
    ],
)
def test_garbage_query_parameters_never_500(client, seeded, query):
    for path in ("/", "/api/results", "/map", "/api/properties.json", "/api/export.csv"):
        response = client.get(f"{path}?{query}")
        assert response.status_code in (200, 422), (path, query, response.status_code)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("banana", None),
        ("-500", AIR_KM_MIN),
        ("5", AIR_KM_MIN),
        ("100000", AIR_KM_MAX),
        ("40", 40.0),
        ("40,5", 40.5),
    ],
)
def test_air_slider_clamps(raw, expected, default_profile):
    profile = profile_from_query({"air_km_max": raw})
    if expected is None:
        assert profile.radius.air_km_max == default_profile.radius.air_km_max
    else:
        assert profile.radius.air_km_max == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", BUDGET_MIN),
        ("-1", BUDGET_MIN),
        ("99999999", BUDGET_MAX),
        ("850000", 850_000.0),
    ],
)
def test_budget_slider_clamps(raw, expected):
    profile = profile_from_query({"total_budget_max": raw})
    assert profile.budget.total_budget_max == expected


def test_budget_slider_actually_moves_the_derived_purchase_bands():
    """config/search.yaml pins purchase_target_max; the slider must still bite."""
    low = profile_from_query({"total_budget_max": "600000"})
    high = profile_from_query({"total_budget_max": "2000000"})
    assert low.budget.effective_purchase_target_max < high.budget.effective_purchase_target_max
    assert low.budget.effective_purchase_target_max == 375_000
    assert low.profile_hash != high.profile_hash


def test_radius_slider_moves_the_derived_driving_limits():
    profile = profile_from_query({"air_km_max": "40"})
    assert profile.radius.effective_driving_soft == pytest.approx(50.0)
    assert profile.radius.effective_driving_hard == pytest.approx(58.0)


# --------------------------------------------------------------------------- #
# The panel collapses on mobile (reported: it buried the first result card).
# --------------------------------------------------------------------------- #


def controls_block(html: str) -> str:
    """The full ``<form id="controls">...</form>`` markup, start to end."""
    start = html.index('<form id="controls"')
    end = html.index("</form>", start) + len("</form>")
    return html[start:end]


def test_toggle_checkbox_starts_unchecked(client, seeded):
    """No JS is required to collapse the panel: it starts closed by markup alone.

    An earlier version used <details>/<summary> for this; a real-browser check
    showed the "force it open past a breakpoint" CSS override does not work
    for <details> (closed content is hidden on an internal slot, not via a
    `display` rule an author stylesheet can override), so the desktop panel
    rendered empty. A hidden checkbox + sibling-shown body does not have that
    problem, so that is what ships - see app.css for the full story.
    """
    form = controls_block(client.get("/").text)
    assert '<input type="checkbox" id="filter-toggle" class="controls__toggle-input"' in form
    assert "checked" not in form[: form.index('id="filter-toggle"')]
    # No name -> never part of the form's own serialised filter state.
    checkbox_start = form.index('id="filter-toggle"')
    checkbox_end = form.index(">", checkbox_start)
    assert "name=" not in form[checkbox_start:checkbox_end]
    assert "checked" not in form[checkbox_start:checkbox_end]


def test_collapsed_label_still_shows_distance_and_budget(client, seeded):
    """Base template's tagline: Entfernung and Gesamtbudget are the two that matter."""
    form = controls_block(client.get("/").text)
    label_start = form.index('<label for="filter-toggle"')
    label_end = form.index("</label>")
    label = form[label_start:label_end]

    assert "Filter" in label
    assert "Entfernung" in label
    assert "Gesamtbudget" in label
    # The actual figures, not just the labels - an id app.js keeps live too.
    assert 'id="out-air-collapsed"' in label
    assert 'id="out-budget-collapsed"' in label


def test_every_control_stays_inside_the_htmx_form(client, seeded):
    """Wrapping the panel in a collapsible body must not move a single input

    out of #controls - hx-trigger serialises the form via FormData, so a
    field left outside it would silently stop being sent to /api/results.
    """
    html = client.get("/").text
    form = controls_block(html)

    for name in (
        "air_km_max",
        "total_budget_max",
        "min_land_sqm",
        "status",
        "q",
        "sort",
        "verified_only",
        "outbuildings_only",
        "include_rejected",
    ):
        assert f'name="{name}"' in form, f"{name!r} missing from #controls"

    # The toggle and the collapsible body both live inside the form.
    assert form.index('<form id="controls"') < form.index('id="filter-toggle"')
    assert form.index('id="controls-body"') < form.index("</form>")


def test_desktop_override_css_still_forces_the_panel_open(client):
    """A rendered-HTML/CSS-text test cannot prove a selector actually paints

    correctly in a browser engine - that needs a real one, and was checked
    manually against headless Chromium (see the task report). What it CAN
    prove is that the pieces the desktop override depends on are still in
    app.css, so nobody can silently delete the escape hatch and leave only
    the mobile-collapsed rule behind.
    """
    css = client.get("/static/app.css").text
    assert ".controls__toggle-input:checked ~ .controls__body { display: block; }" in css

    # Reuse the same breakpoint .controls__headline already switches on.
    marker = "@media (min-width: 780px) {\n  .controls__toggle-input"
    assert marker in css
    block_start = css.index(marker)
    block_end = css.index("\n}", block_start)
    desktop_block = css[block_start:block_end]
    assert ".controls__toggle-input, .controls__toggle { display: none; }" in desktop_block
    assert ".controls__body { display: block" in desktop_block
