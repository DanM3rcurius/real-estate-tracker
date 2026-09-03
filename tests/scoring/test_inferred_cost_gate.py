"""A modelled cost may flag a property, never remove it from the shortlist.

Every genuine Hofstelle is pre-1960 with the condition unstated, so the
pessimistic age rule applies to a large building and the modelled total clears
the hard maximum on properties whose asking price is comfortably inside budget.
Rejecting on that number makes the system blind to its own target class.
"""

from __future__ import annotations

from factories import make_profile, make_property

from hofradar.scoring.engine import FLAG_COST_INFERRED, REJECT_TOTAL_COST, score_property


def test_inferred_cost_over_budget_flags_but_does_not_reject() -> None:
    profile = make_profile(total_budget_hard_max=800_000)
    # Pre-1960, no stated condition: tier HEAVY by the age rule alone.
    prop = make_property(price=350_000, living_sqm=400, year_built=1890)

    result = score_property(prop, profile)

    assert REJECT_TOTAL_COST not in result.reject_reasons
    assert FLAG_COST_INFERRED in result.flags


def test_observed_cost_over_budget_still_rejects() -> None:
    profile = make_profile(total_budget_hard_max=800_000)
    prop = make_property(
        price=350_000, living_sqm=400, year_built=1890, condition="sanierungsbeduerftig"
    )

    result = score_property(prop, profile)

    assert REJECT_TOTAL_COST in result.reject_reasons
