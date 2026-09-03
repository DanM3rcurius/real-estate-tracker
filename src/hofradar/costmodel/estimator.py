"""Total-cost model for a Bavarian farmstead.

Why a farmstead is not ``living_sqm * EUR/m2``
==============================================

The usual portal arithmetic - habitable square metres times a renovation rate -
is wrong for a Hofstelle by a factor of two or more, because most of what has to
be paid for is not habitable square metres:

* the **roof** over the whole footprint (the Stadel is under the same ridge),
* the **outbuildings**, which are counted in the listing as an asset and in the
  builder's quote as a liability,
* the **utilities** - heating, electrics, water, waste water - which are a
  lump sum on a building that has none of them to current standard,
* a **contingency**, because the opened wall always costs more than the drawing.

So the estimate is assembled from components. Every component is written into
``CostResult.breakdown`` and every assumption behind it into
``CostResult.assumptions`` as a sentence a human can argue with. No number in
this module is invented inside a function body: rates come from
``profile.renovation`` and geometry from the documented constants below.

The output depends on facts and on ``profile.renovation`` only, so it is cached
once per property in ``CostEstimate`` (see :mod:`hofradar.db.models`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hofradar.contracts import CostResult
from hofradar.costmodel._text import fold_all
from hofradar.costmodel.renovation import infer_renovation_tier, renovation_evidence
from hofradar.db.enums import RenovationTier

if TYPE_CHECKING:  # pragma: no cover
    from hofradar.config import SearchProfile
    from hofradar.db.models import Property

# --------------------------------------------------------------------------- #
# Documented geometry assumptions (the config owns the money, this owns the m2)
# --------------------------------------------------------------------------- #

#: Assumed habitable area when the listing does not state one. Typical main
#: dwelling of a Bavarian Einfirsthof / Sacherl.
DEFAULT_LIVING_SQM = 180.0
#: Share of a stated ``usable_sqm`` (Nutzflaeche, which includes the Stadel)
#: that is actually habitable. Used only when ``living_sqm`` is missing.
USABLE_TO_LIVING_FRACTION = 0.45
#: Habitable area is spread over roughly this many storeys, so the ground
#: footprint - and therefore the roof - is ``living_sqm / storeys``.
LIVING_TO_FOOTPRINT_STOREYS = 1.6
#: A farmstead roof spans the barn as well as the dwelling; the ridge is one
#: continuous run. Footprint under roof is this multiple of the house footprint.
ROOF_FOOTPRINT_FACTOR = 1.5
#: Typical footprints of Bavarian outbuildings, in square metres.
OUTBUILDING_SQM: dict[str, float] = {
    "scheune": 200.0,
    "stadel": 180.0,
    "stadl": 180.0,
    "stall": 150.0,
    "tenne": 120.0,
    "remise": 60.0,
    "schuppen": 60.0,
    "wagenremise": 60.0,
    "garage": 40.0,
}
#: Any other outbuilding tag we do not recognise.
DEFAULT_OUTBUILDING_SQM = 100.0

#: Renovation rate bands, tier -> (min attribute, max attribute) on RenovationRates.
_TIER_RATE_FIELDS: dict[RenovationTier, tuple[str, str]] = {
    RenovationTier.LIGHT: ("light_min", "light_max"),
    RenovationTier.MEDIUM: ("medium_min", "medium_max"),
    RenovationTier.HEAVY: ("heavy_min", "heavy_max"),
    RenovationTier.COMPLETE: ("complete_min", "complete_max"),
    #: An unknown tier is priced as HEAVY - see the module docstring of
    #: :mod:`hofradar.costmodel.renovation` for why we round upwards.
    RenovationTier.UNKNOWN: ("heavy_min", "heavy_max"),
}


def acquisition_costs(price: float | None, profile: SearchProfile) -> float:
    """Bavarian side costs: Grunderwerbsteuer, Notar, Grundbuch, Makler.

    All four are percentages of the purchase price and all four live in
    ``profile.budget``, so a user who buys without a broker simply sets
    ``makler_pct`` to zero and every downstream score follows.
    """
    if not price or price <= 0:
        return 0.0
    return round(price * profile.budget.acquisition_cost_pct, 2)


def _tier_rates(tier: RenovationTier, profile: SearchProfile) -> tuple[float, float, float]:
    """(low, mid, high) EUR per square metre for a tier, from the config."""
    low_field, high_field = _TIER_RATE_FIELDS[tier]
    low = float(getattr(profile.renovation, low_field))
    high = float(getattr(profile.renovation, high_field))
    return low, (low + high) / 2.0, high


def _living_sqm(prop: Property, assumptions: list[str]) -> float:
    """Habitable area, with a documented fallback chain."""
    living = getattr(prop, "living_sqm", None)
    if living and living > 0:
        return float(living)
    usable = getattr(prop, "usable_sqm", None)
    if usable and usable > 0:
        derived = float(usable) * USABLE_TO_LIVING_FRACTION
        assumptions.append(
            f"Living area not stated; assumed {derived:.0f} m2, i.e. "
            f"{USABLE_TO_LIVING_FRACTION:.0%} of the stated usable area of {usable:.0f} m2."
        )
        return derived
    assumptions.append(
        f"Neither living nor usable area stated; assumed the typical Bavarian "
        f"farmhouse dwelling of {DEFAULT_LIVING_SQM:.0f} m2."
    )
    return DEFAULT_LIVING_SQM


def _outbuilding_sqm(prop: Property, assumptions: list[str]) -> tuple[float, dict[str, float]]:
    """Total outbuilding area from the tag list, using typical footprints."""
    per_tag: dict[str, float] = {}
    for tag in fold_all(getattr(prop, "outbuildings", None)):
        matched = next((key for key in OUTBUILDING_SQM if key in tag), None)
        sqm = OUTBUILDING_SQM[matched] if matched else DEFAULT_OUTBUILDING_SQM
        per_tag[tag] = per_tag.get(tag, 0.0) + sqm
    if per_tag:
        detail = ", ".join(f"{tag} {sqm:.0f} m2" for tag, sqm in sorted(per_tag.items()))
        assumptions.append(f"Outbuilding areas assumed from typical Bavarian sizes: {detail}.")
    else:
        assumptions.append("No outbuildings tagged; no outbuilding renovation budgeted.")
    return sum(per_tag.values()), per_tag


def estimate_costs(prop: Property, profile: SearchProfile) -> CostResult:
    """Assemble a low / mid / high total cost of ownership for one property.

    ``total_x = purchase + acquisition + renovation_x + immediate_capex``.

    Only the house component varies across the band (it is the one driven by an
    EUR/m2 rate); roof, outbuildings and utilities are lump sums that do not get
    cheaper because the survey was optimistic. The contingency is a percentage
    of whichever band it belongs to, so the high case carries a high buffer.
    """
    rates = profile.renovation
    assumptions: list[str] = []

    tier = infer_renovation_tier(prop)
    rate_low, rate_mid, rate_high = _tier_rates(tier, profile)
    assumptions.append(
        f"Renovation tier {tier.value.upper()} at {rate_low:.0f}-{rate_high:.0f} EUR/m2 "
        f"of living area."
    )

    living_sqm = _living_sqm(prop, assumptions)
    house_low = living_sqm * rate_low
    house_mid = living_sqm * rate_mid
    house_high = living_sqm * rate_high

    house_footprint = living_sqm / LIVING_TO_FOOTPRINT_STOREYS
    roof_sqm = house_footprint * ROOF_FOOTPRINT_FACTOR
    roof = roof_sqm * rates.roof_per_sqm_footprint
    assumptions.append(
        f"Roof: {living_sqm:.0f} m2 living area over {LIVING_TO_FOOTPRINT_STOREYS} storeys gives a "
        f"{house_footprint:.0f} m2 house footprint; the continuous farmstead ridge covers "
        f"{ROOF_FOOTPRINT_FACTOR}x that, so {roof_sqm:.0f} m2 at "
        f"{rates.roof_per_sqm_footprint:.0f} EUR/m2."
    )

    outbuilding_sqm, per_outbuilding = _outbuilding_sqm(prop, assumptions)
    outbuildings = outbuilding_sqm * rates.outbuilding_per_sqm
    if outbuilding_sqm:
        assumptions.append(
            f"Outbuildings: {outbuilding_sqm:.0f} m2 at "
            f"{rates.outbuilding_per_sqm:.0f} EUR/m2 to make them weathertight and usable."
        )

    utilities = float(rates.utilities_base)
    assumptions.append(
        f"Utilities (heating, electrics, water, waste water) as a lump sum of "
        f"{utilities:,.0f} EUR, independent of size."
    )

    base_low = house_low + roof + outbuildings + utilities
    base_mid = house_mid + roof + outbuildings + utilities
    base_high = house_high + roof + outbuildings + utilities
    contingency_low = base_low * rates.contingency_pct
    contingency_mid = base_mid * rates.contingency_pct
    contingency_high = base_high * rates.contingency_pct
    assumptions.append(
        f"Contingency of {rates.contingency_pct:.0%} on every renovation component."
    )

    renovation_low = base_low + contingency_low
    renovation_mid = base_mid + contingency_mid
    renovation_high = base_high + contingency_high

    immediate_capex = float(rates.immediate_capex_base)
    assumptions.append(
        f"Immediate capex of {immediate_capex:,.0f} EUR before the building is usable at all "
        f"(access, securing the structure, emergency repairs)."
    )

    price = getattr(prop, "price", None)
    purchase = float(price) if price else 0.0
    if not price:
        assumptions.append(
            "No asking price known; totals below are renovation and capex only and are "
            "a lower bound, not an estimate."
        )
    acquisition = acquisition_costs(purchase, profile)
    assumptions.append(
        f"Acquisition side costs of {profile.budget.acquisition_cost_pct:.2%} of the purchase "
        f"price (Grunderwerbsteuer, Notar, Grundbuch, Makler) = {acquisition:,.0f} EUR."
    )

    fixed = purchase + acquisition + immediate_capex
    breakdown: dict[str, float] = {
        "purchase": round(purchase, 2),
        "acquisition": round(acquisition, 2),
        "house": round(house_mid, 2),
        "house_low": round(house_low, 2),
        "house_high": round(house_high, 2),
        "roof": round(roof, 2),
        "outbuildings": round(outbuildings, 2),
        "utilities": round(utilities, 2),
        "contingency": round(contingency_mid, 2),
        "contingency_low": round(contingency_low, 2),
        "contingency_high": round(contingency_high, 2),
        "immediate_capex": round(immediate_capex, 2),
        "living_sqm_used": round(living_sqm, 1),
        "roof_sqm_used": round(roof_sqm, 1),
        "outbuilding_sqm_used": round(outbuilding_sqm, 1),
        "rate_per_sqm_low": rate_low,
        "rate_per_sqm_mid": rate_mid,
        "rate_per_sqm_high": rate_high,
    }
    breakdown.update({f"outbuilding_sqm_{tag}": sqm for tag, sqm in per_outbuilding.items()})

    return CostResult(
        purchase_price=float(price) if price else None,
        acquisition_costs=round(acquisition, 2),
        renovation_low=round(renovation_low, 2),
        renovation_mid=round(renovation_mid, 2),
        renovation_high=round(renovation_high, 2),
        immediate_capex=round(immediate_capex, 2),
        total_low=round(fixed + renovation_low, 2),
        total_mid=round(fixed + renovation_mid, 2),
        total_high=round(fixed + renovation_high, 2),
        renovation_tier=tier.value,
        renovation_evidence=renovation_evidence(prop),
        breakdown=breakdown,
        assumptions=assumptions,
    )
