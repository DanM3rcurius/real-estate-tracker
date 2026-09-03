"""Is this a *sane* deal - measured against the user's own capital, not a market.

The blueprint's Capital Risk Gate is written in absolute euros. Absolute euros
stop being true the moment the user drags the budget slider, so every threshold
here is expressed as a fraction of ``profile.budget``:

======================================  =====================================
blueprint                               here
======================================  =====================================
"total cost above the budget"           ``total_mid / budget.total_budget_max``
"far above the budget - exclude"        ``budget.effective_total_hard_max``
"only if exceptional"                   ``budget.effective_total_exceptional_max``
"renovation dwarfs the purchase"        ``gates.renovation_to_price_risk_ratio``
======================================  =====================================

The euro-per-square-metre reference prices are derived from the sliders too: a
plot of ``land.strong_min_sqm`` bought at ``budget.effective_purchase_target_max``
is by definition the most the user can pay per square metre of land and still be
on target, and the same trick with :data:`REFERENCE_LIVING_SQM` gives the
reference price per square metre of living space. Both therefore move with the
budget slider instead of encoding a market snapshot that ages badly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hofradar.contracts import CostResult
from hofradar.db.enums import CapitalRisk
from hofradar.scoring._util import band

if TYPE_CHECKING:  # pragma: no cover
    from hofradar.config import SearchProfile
    from hofradar.db.models import Property

# --------------------------------------------------------------------------- #
# Component budgets (sum = 100)
# --------------------------------------------------------------------------- #

TOTAL_COST_MAX = 40.0
LAND_PRICE_MAX = 20.0
LIVING_PRICE_MAX = 20.0
RENOVATION_RATIO_MAX = 20.0

#: What fraction of a component is awarded when the fact it needs is missing.
#: Not zero (that would punish a thin listing twice - confidence already does)
#: and not full (an unknown plot size is not evidence of a good price).
UNKNOWN_COMPONENT_FRACTION = 0.4

#: (fraction of budget.total_budget_max, points).
TOTAL_COST_BANDS: tuple[tuple[float, float], ...] = (
    (0.75, 40.0),
    (0.90, 34.0),
    (1.00, 28.0),
)
#: Points once the total cost is past the budget but still inside the
#: exceptional / hard band. Both bounds come from BudgetConfig.
TOTAL_COST_EXCEPTIONAL_POINTS = 14.0
TOTAL_COST_HARD_POINTS = 6.0

#: A dwelling of this size is the reference the price-per-living-m2 band is
#: built on: the main house of a Bavarian farmstead.
REFERENCE_LIVING_SQM = 200.0
#: (fraction of the reference EUR/m2, points).
PRICE_PER_SQM_BANDS: tuple[tuple[float, float], ...] = (
    (0.40, 1.00),
    (0.70, 0.80),
    (1.00, 0.55),
    (1.50, 0.25),
)
#: (multiple of gates.renovation_to_price_risk_ratio, points fraction).
RENOVATION_RATIO_BANDS: tuple[tuple[float, float], ...] = (
    (0.25, 1.00),
    (0.50, 0.80),
    (0.75, 0.50),
    (1.00, 0.20),
)

#: Above this fraction of the budget the capital risk stops being LOW even
#: though nothing has been breached yet.
MODERATE_RISK_FRACTION = 0.90

#: A property the user cannot pay for is not a good deal at any price per square
#: metre, so an EXTREME capital risk caps the whole score rather than merely
#: zeroing the total-cost component.
EXTREME_RISK_DEAL_CEILING = 10.0

SANIERUNGSRISIKO_FLAG = "SANIERUNGSRISIKO"
OVER_BUDGET_FLAG = "OVER_BUDGET"
EXCEPTIONAL_BUDGET_FLAG = "EXCEPTIONAL_BUDGET_BAND"


def _total_cost_component(
    cost: CostResult, profile: SearchProfile, out: dict[str, Any]
) -> tuple[float, str]:
    budget = profile.budget
    total_mid = float(cost.total_mid or 0.0)
    ratio = total_mid / budget.total_budget_max
    out["total_mid"] = round(total_mid, 2)
    out["total_budget_ratio"] = round(ratio, 4)
    out["total_budget_max"] = budget.total_budget_max
    out["total_exceptional_max"] = budget.effective_total_exceptional_max
    out["total_hard_max"] = budget.effective_total_hard_max

    if total_mid > budget.effective_total_hard_max:
        return 0.0, CapitalRisk.EXTREME
    exceptional_ratio = budget.effective_total_exceptional_max / budget.total_budget_max
    hard_ratio = budget.effective_total_hard_max / budget.total_budget_max
    bands = (
        *TOTAL_COST_BANDS,
        (exceptional_ratio, TOTAL_COST_EXCEPTIONAL_POINTS),
        (hard_ratio, TOTAL_COST_HARD_POINTS),
    )
    points = band(ratio, bands)
    if total_mid > budget.total_budget_max:
        risk = CapitalRisk.HIGH
    elif ratio > MODERATE_RISK_FRACTION:
        risk = CapitalRisk.MODERATE
    else:
        risk = CapitalRisk.LOW
    return points, risk


def _land_price_component(
    prop: Property, profile: SearchProfile, out: dict[str, Any]
) -> float:
    """Price per square metre of land against a slider-derived reference."""
    reference = profile.budget.effective_purchase_target_max / profile.land.strong_min_sqm
    out["land_price_reference_eur_per_sqm"] = round(reference, 2)
    price = getattr(prop, "price", None)
    land_sqm = getattr(prop, "land_sqm", None)
    if not price or not land_sqm:
        out["price_per_land_sqm"] = None
        return LAND_PRICE_MAX * UNKNOWN_COMPONENT_FRACTION
    per_sqm = float(price) / float(land_sqm)
    out["price_per_land_sqm"] = round(per_sqm, 2)
    return LAND_PRICE_MAX * band(per_sqm / reference, PRICE_PER_SQM_BANDS)


def _living_price_component(
    prop: Property, profile: SearchProfile, out: dict[str, Any]
) -> float:
    """Price per square metre of living space against a slider-derived reference."""
    reference = profile.budget.effective_purchase_target_max / REFERENCE_LIVING_SQM
    out["living_price_reference_eur_per_sqm"] = round(reference, 2)
    price = getattr(prop, "price", None)
    living_sqm = getattr(prop, "living_sqm", None)
    if not price or not living_sqm:
        out["price_per_living_sqm"] = None
        return LIVING_PRICE_MAX * UNKNOWN_COMPONENT_FRACTION
    per_sqm = float(price) / float(living_sqm)
    out["price_per_living_sqm"] = round(per_sqm, 2)
    return LIVING_PRICE_MAX * band(per_sqm / reference, PRICE_PER_SQM_BANDS)


def _renovation_ratio_component(
    cost: CostResult, profile: SearchProfile, out: dict[str, Any]
) -> tuple[float, bool]:
    """Renovation against purchase price. Returns (points, sanierungsrisiko)."""
    risk_ratio = profile.gates.renovation_to_price_risk_ratio
    out["renovation_risk_ratio_limit"] = risk_ratio
    purchase = float(cost.purchase_price or 0.0)
    renovation = float(cost.renovation_mid or 0.0)
    if purchase <= 0:
        out["renovation_to_price_ratio"] = None
        return RENOVATION_RATIO_MAX * UNKNOWN_COMPONENT_FRACTION, False
    ratio = renovation / purchase
    out["renovation_to_price_ratio"] = round(ratio, 4)
    fraction = band(ratio / risk_ratio, RENOVATION_RATIO_BANDS)
    return RENOVATION_RATIO_MAX * fraction, ratio > risk_ratio


def deal_score(
    prop: Property, profile: SearchProfile, cost: CostResult | None = None
) -> tuple[float, dict[str, Any]]:
    """Score 0-100, plus ``capital_risk`` and any risk flags in the breakdown.

    The breakdown carries two keys the engine reads back:
    ``capital_risk`` (a :class:`CapitalRisk` value) and ``flags``.
    """
    from hofradar.costmodel import estimate_costs  # local: avoids an import cycle

    cost = cost if cost is not None else estimate_costs(prop, profile)
    out: dict[str, Any] = {}
    flags: list[str] = []

    total_points, risk = _total_cost_component(cost, profile, out)
    land_points = _land_price_component(prop, profile, out)
    living_points = _living_price_component(prop, profile, out)
    renovation_points, sanierungsrisiko = _renovation_ratio_component(cost, profile, out)

    budget = profile.budget
    total_mid = float(cost.total_mid or 0.0)
    if total_mid > budget.total_budget_max:
        flags.append(OVER_BUDGET_FLAG)
    if budget.effective_total_exceptional_max < total_mid <= budget.effective_total_hard_max:
        flags.append(EXCEPTIONAL_BUDGET_FLAG)
    if sanierungsrisiko:
        flags.append(SANIERUNGSRISIKO_FLAG)
        #: A renovation that dwarfs the purchase is a capital risk in its own
        #: right, whatever the headline total happens to be.
        if risk in (CapitalRisk.LOW, CapitalRisk.MODERATE):
            risk = CapitalRisk.HIGH

    out.update(
        {
            "total_cost_score": round(total_points, 2),
            "total_cost_max": TOTAL_COST_MAX,
            "land_price_score": round(land_points, 2),
            "land_price_max": LAND_PRICE_MAX,
            "living_price_score": round(living_points, 2),
            "living_price_max": LIVING_PRICE_MAX,
            "renovation_ratio_score": round(renovation_points, 2),
            "renovation_ratio_max": RENOVATION_RATIO_MAX,
            "capital_risk": risk.value,
            "flags": flags,
        }
    )
    total = total_points + land_points + living_points + renovation_points
    if risk is CapitalRisk.EXTREME and total > EXTREME_RISK_DEAL_CEILING:
        out["extreme_risk_cap"] = EXTREME_RISK_DEAL_CEILING
        out["uncapped_score"] = round(total, 2)
        total = EXTREME_RISK_DEAL_CEILING
    return round(total, 2), out
