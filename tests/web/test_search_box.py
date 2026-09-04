"""Issue #14: the search box means "find what I typed", not "exactly this town"."""

from __future__ import annotations

import pytest

from hofradar.search import matches_search


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


def test_search_survives_when_scoring_is_unavailable(client, seeded, monkeypatch):
    """Fix round 1 on issue #14: a broken/missing scoring package must not turn
    an active search term into a 500 on the fallback (unscored) path.

    ``hofradar.web.lazy``'s own module docstring promises the radar "must
    boot even when [...] scoring [...] [is] missing, half-written or raising
    on import" and degrades into a German notice instead of a 500 - exactly
    the ``ModuleUnavailable``/``Degraded`` machinery ``tests/web/
    test_lazy_messages.py`` exercises directly. Here ``hofradar.scoring`` and
    ``hofradar.scoring.engine`` are put into ``sys.modules`` as ``None``,
    which forces *every* subsequent import of either name to raise
    ``ImportError`` - both the ones ``lazy.load`` wraps (so ``_rescore`` and
    ``ranked_properties`` degrade gracefully and ``build_results`` falls back
    to ``_fallback_pairs``) and, before this fix, the plain
    ``from hofradar.scoring.engine import matches_search`` that
    ``passes_filters`` used to do at call time - which was not behind
    ``lazy`` at all and would have propagated straight into a 500. The fix is
    that ``matches_search`` now lives in ``hofradar.search``, already
    imported at module load time and with no dependency on
    ``hofradar.scoring``, so the degraded path never touches the broken
    package again.
    """
    import sys

    monkeypatch.setitem(sys.modules, "hofradar.scoring", None)
    monkeypatch.setitem(sys.modules, "hofradar.scoring.engine", None)

    response = client.get("/api/results?q=Traun")
    assert response.status_code == 200
    assert "HF-0002" in response.text
