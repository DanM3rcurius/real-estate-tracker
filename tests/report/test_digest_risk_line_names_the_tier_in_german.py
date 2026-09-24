"""The digest's Sanierungsstufe risk line is German, not the enum's English word.

``_risks`` names a heavy or complete renovation tier as a risk line, and it
must run the tier through ``web.filters.de_tier`` rather than printing the
raw ``CostResult.renovation_tier`` string - "Sanierungsstufe heavy" is not a
sentence a reader can act on. Exercised against the private helper directly
(CLAUDE.md's own suggestion), since assembling a full scored, ranked property
just to reach one risk line would obscure what is actually being asserted.
"""

from __future__ import annotations

from hofradar.config import SearchProfile
from hofradar.contracts import CostResult
from hofradar.report.data import _risks


def test_a_heavy_tier_is_named_in_german(make_property) -> None:
    prop = make_property()
    profile = SearchProfile()
    cost = CostResult(renovation_tier="heavy")

    risks = _risks(prop, None, cost, profile)

    assert "Sanierungsstufe schwer" in risks
    assert not any("heavy" in risk for risk in risks)


def test_a_complete_tier_is_named_in_german(make_property) -> None:
    prop = make_property()
    profile = SearchProfile()
    cost = CostResult(renovation_tier="complete")

    risks = _risks(prop, None, cost, profile)

    assert "Sanierungsstufe Kernsanierung" in risks


def test_a_light_or_medium_tier_is_not_listed_as_a_risk_at_all(make_property) -> None:
    prop = make_property()
    profile = SearchProfile()

    for tier in ("light", "medium"):
        cost = CostResult(renovation_tier=tier)
        risks = _risks(prop, None, cost, profile)
        assert not any("Sanierungsstufe" in risk for risk in risks)
