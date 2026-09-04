"""Ranking: turning facts plus the user's two sliders into a defensible order.

Why every threshold in this package is a fraction
=================================================

The blueprint states its thresholds in absolute units - "within 30 km", "under
400,000 EUR", "reject above 1.5 million". Absolute thresholds silently stop
being right the moment the user drags the **distance** or the **budget** slider,
which is the one interaction this whole application exists to support. So every
absolute threshold is stored here as a *fraction of the relevant slider* and
multiplied back out at scoring time:

===========================  ==========================================  =============
blueprint (absolute)         expressed here as                            default value
===========================  ==========================================  =============
30 / 50 / 65 / 80 km         30/80, 50/80, 65/80, 1.0 of ``air_km_max``   80 km
400 / 550 / 650 / 750k EUR   8/15, 11/15, 13/15, 1.0 of the purchase      750,000 EUR
                             target (``effective_purchase_target_max``)
850k EUR negotiation band    ``effective_purchase_negotiation_max``       850,000 EUR
900k EUR hard price limit    ``effective_purchase_hard_max``              900,000 EUR
2,000 / 5,000 m2 plot        multiples of ``land.preferred_min_sqm``      2,000 m2
1.2M EUR total budget        ``budget.total_budget_max``                  1,200,000 EUR
1.35M "exceptional only"     ``effective_total_exceptional_max``          1,350,000 EUR
1.5M "excluded"              ``effective_total_hard_max``                 1,500,000 EUR
115 km driving limit         ``radius.effective_driving_hard``            116 km
===========================  ==========================================  =============

With the shipped defaults every fraction reproduces the blueprint's absolute
number exactly - ``tests/scoring/test_blueprint_bands.py`` asserts it - and with
the slider anywhere else the same relative judgement still holds.

Public surface, per ``docs/MODULE_API.md``::

    score_property(prop, profile, *, cost=None, now=None) -> ScoreResult
    fit_score(prop, profile) -> tuple[float, dict]
    deal_score(prop, profile, cost) -> tuple[float, dict]
    hidden_score(prop, profile, now) -> tuple[float, dict]
    freshness_score(prop, now) -> tuple[float, dict]
    confidence_score(prop) -> tuple[float, dict]
    rescore_all(session, profile, *, only_dirty=True) -> int
    ranked_properties(session, profile, *, limit=None, include_rejected=False,
                      include_hidden=False, filters=None) -> list[tuple[Property, Score]]
"""

from __future__ import annotations

from hofradar.scoring.deal import deal_score
from hofradar.scoring.engine import ranked_properties, rescore_all, score_property
from hofradar.scoring.fit import fit_score
from hofradar.scoring.signals import confidence_score, freshness_score, hidden_score

__all__ = [
    "confidence_score",
    "deal_score",
    "fit_score",
    "freshness_score",
    "hidden_score",
    "ranked_properties",
    "rescore_all",
    "score_property",
]
