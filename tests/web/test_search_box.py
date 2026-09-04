"""Issue #14: the search box means "find what I typed", not "exactly this town"."""

from __future__ import annotations

import pytest

from hofradar.scoring.engine import matches_search


@pytest.mark.parametrize("needle", ["83278", "Traun", "traunstein", "Vierseithof", "Chiemgau"])
def test_search_box_finds_the_property(client, seeded, needle):
    payload = client.get(f"/api/properties.json?q={needle}").json()
    ids = [p["public_id"] for p in payload["properties"]]
    assert ids == ["HF-0002"], (needle, ids)


def test_search_box_miss_says_zero_hits(client, seeded):
    html = client.get("/api/results?q=Nirgendwo").text
    assert "0 Treffer für „Nirgendwo“" in html
    assert "Kein Objekt passt zu diesen Reglern" not in html


def test_statusline_names_the_active_search(client, seeded):
    assert "Suche „Traun“" in client.get("/api/results?q=Traun").text


def test_umlauts_match_case_insensitively(db, seeded):
    from tests.web.conftest import make_property

    prop = make_property(db, public_id="HF-0200", town="Ödhof", canonical_title="Hof")
    assert matches_search(prop, "ödhof")
    assert matches_search(prop, "ÖD")
    assert not matches_search(prop, "Miesbach")
