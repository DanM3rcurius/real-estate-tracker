"""How well a property matches what the user is actually looking for.

Every band in this module is a **fraction of a slider**, never an absolute
euro or kilometre value - see the package docstring of
:mod:`hofradar.scoring` for the conversion table and why it exists.

``fit_score`` is the sum of seven components that add up to 100:

===================  ====  ==========================================
component            pts   driven by
===================  ====  ==========================================
geography             15   distance_air_km / radius.air_km_max
price                 20   price / budget.effective_purchase_target_max
land                  15   land_sqm / land.preferred_min_sqm
farmstead substance   20   what kind of building this is
seclusion             10   Alleinlage ... dense village
development           10   provable divisibility ... unclear
outbuildings          10   Scheune / Stall / Tenne
===================  ====  ==========================================
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from hofradar.scoring._util import band, band_below, contains_any, text_blob

if TYPE_CHECKING:  # pragma: no cover
    from hofradar.config import SearchProfile
    from hofradar.db.models import Property

# --------------------------------------------------------------------------- #
# Geography - fractions of radius.air_km_max
# --------------------------------------------------------------------------- #

GEOGRAPHY_MAX = 15.0
#: (fraction of the air radius, points). With the default 80 km slider these
#: fractions are exactly the blueprint's 30 / 50 / 65 / 80 km bands.
GEOGRAPHY_BANDS: tuple[tuple[float, float], ...] = (
    (30 / 80, 15.0),
    (50 / 80, 13.0),
    (65 / 80, 10.0),
    (1.0, 6.0),
)

# --------------------------------------------------------------------------- #
# Price - fractions of budget.effective_purchase_target_max
# --------------------------------------------------------------------------- #

PRICE_MAX = 20.0
#: (fraction of the purchase target, points). With the default 750k target these
#: are exactly the blueprint's 400k / 550k / 650k / 750k bands; the fifth band
#: is the negotiation ceiling, which is itself derived from the same slider.
PRICE_BANDS: tuple[tuple[float, float], ...] = (
    (400_000 / 750_000, 20.0),
    (550_000 / 750_000, 18.0),
    (650_000 / 750_000, 16.0),
    (1.0, 13.0),
)
#: Points for a price between the target and the negotiation ceiling. Cheapness
#: below the top band earns nothing extra - a bargain is a deal_score question.
PRICE_NEGOTIATION_POINTS = 5.0

# --------------------------------------------------------------------------- #
# Land - multiples of land.preferred_min_sqm
# --------------------------------------------------------------------------- #

LAND_MAX = 15.0
#: (multiple of preferred_min_sqm, points), exclusive thresholds. With the
#: default 2000 m2 these are 1000 / 2000 / 4000 / 8000 m2. ``strong_min_sqm``
#: is reported alongside as the "this is a proper Hofstelle plot" marker.
LAND_BANDS: tuple[tuple[float, float], ...] = (
    (0.5, 0.0),
    (1.0, 5.0),
    (2.0, 10.0),
    (4.0, 13.0),
)
LAND_TOP_POINTS = 15.0

# --------------------------------------------------------------------------- #
# Farmstead substance
# --------------------------------------------------------------------------- #

SUBSTANCE_MAX = 20.0
SUBSTANCE_CORE_POINTS = 10.0
SUBSTANCE_HISTORIC_POINTS = 5.0
SUBSTANCE_BARN_POINTS = 3.0
SUBSTANCE_STABLE_POINTS = 2.0

CORE_FARMSTEAD_TERMS: tuple[str, ...] = (
    "sacherl",
    "hofstelle",
    "hofanwesen",
    "bauernhof",
    "resthof",
    "vierseithof",
    "dreiseithof",
    "einoedhof",
    "einfirsthof",
    "landwirtschaftliches anwesen",
    "aussiedlerhof",
    "austragshaus",
)
HISTORIC_TERMS: tuple[str, ...] = (
    "denkmal",
    "historisch",
    "historische bausubstanz",
    "jahrhundertwende",
    "fachwerk",
    "blockbau",
    "ensembleschutz",
    "bauernhaus von",
)
#: Built before this counts as historic substance on its own.
HISTORIC_YEAR = 1900
BARN_TERMS: tuple[str, ...] = ("stadl", "stadel", "scheune", "scheuer")
STABLE_TERMS: tuple[str, ...] = ("stall", "tenne", "kuhstall", "pferdestall")

# --------------------------------------------------------------------------- #
# Seclusion (Alleinlage)
# --------------------------------------------------------------------------- #

SECLUSION_MAX = 10.0
ALLEINLAGE_TERMS: tuple[str, ...] = (
    "alleinlage",
    "einoede",
    "einoedhof",
    "einzellage",
    "solitaerlage",
    "abgeschiedene lage",
    "keine nachbarn",
    "freie feldlage",
)
ORTSRAND_TERMS: tuple[str, ...] = ("ortsrand", "ortsrandlage", "randlage", "am dorfrand")
LOOSE_TERMS: tuple[str, ...] = (
    "weiler",
    "streusiedlung",
    "aufgelockerte bebauung",
    "lockere bebauung",
    "einzelgehoeft",
    "kleiner ortsteil",
)
ALLEINLAGE_POINTS = 10.0
ORTSRAND_POINTS = 7.0
LOOSE_POINTS = 4.0
#: No evidence of seclusion is scored as a dense village, not as unknown: a
#: farmstead that is genuinely alone is always advertised as such.
DENSE_POINTS = 1.0

# --------------------------------------------------------------------------- #
# Development potential
# --------------------------------------------------------------------------- #

DEVELOPMENT_MAX = 10.0
#: Statements that carry their own proof - an authority has already decided.
PROVABLE_TERMS: tuple[str, ...] = (
    "teilungsgenehmigung",
    "teilung genehmigt",
    "grundstueck ist geteilt",
    "zwei bauplaetze",
    "zwei baugrundstuecke",
    "zweites baufenster",
    "baugenehmigung liegt vor",
    "positiver bauvorbescheid",
)
#: A concrete, checkable reference to building law.
BAURECHT_TERMS: tuple[str, ...] = (
    "bebauungsplan",
    "b plan",
    "34 baugb",
    "35 baugb",
    "bauvorbescheid",
    "baugenehmigung",
    "baurecht",
    "innenbereich",
    "flaechennutzungsplan",
)
#: A broker adjective. Plausible, unproven, and worth exactly four points.
PLAUSIBLE_TERMS: tuple[str, ...] = (
    "entwicklungspotenzial",
    "entwicklungsmoeglichkeit",
    "teilbar",
    "teilung moeglich",
    "erweiterbar",
    "ausbaureserve",
    "bauerwartungsland",
    "nachverdichtung",
    "potenzial",
)
#: ``Property.evidence`` keys that turn a mere divisibility claim into a proof.
DIVISIBILITY_EVIDENCE_KEYS: tuple[str, ...] = ("divisible", "teilbar", "teilung", "baurecht")
DEVELOPMENT_PROVABLE_POINTS = 10.0
DEVELOPMENT_BAURECHT_POINTS = 7.0
DEVELOPMENT_PLAUSIBLE_POINTS = 4.0

# --------------------------------------------------------------------------- #
# Outbuildings
# --------------------------------------------------------------------------- #

OUTBUILDING_MAX = 10.0
#: Canonical outbuilding type -> the terms that denote it.
OUTBUILDING_TYPES: dict[str, tuple[str, ...]] = {
    "scheune": ("scheune", "stadl", "stadel", "scheuer"),
    "stall": ("stall",),
    "tenne": ("tenne",),
}
#: Points by number of distinct types present.
OUTBUILDING_POINTS: dict[int, float] = {0: 0.0, 1: 4.0, 2: 7.0, 3: 10.0}


def _geography(prop: Property, profile: SearchProfile, out: dict[str, Any]) -> float:
    distance = getattr(prop, "distance_air_km", None)
    limit = profile.radius.air_km_max
    if distance is None:
        out["geography_note"] = "distance unknown - no geography points awarded"
        out["geography_ratio"] = None
        return 0.0
    ratio = float(distance) / limit
    out["geography_ratio"] = round(ratio, 4)
    return band(ratio, GEOGRAPHY_BANDS)


def _price(prop: Property, profile: SearchProfile, out: dict[str, Any]) -> float:
    price = getattr(prop, "price", None)
    target = profile.budget.effective_purchase_target_max
    if not price:
        out["price_note"] = "no asking price known - no price points awarded"
        out["price_ratio"] = None
        return 0.0
    ratio = float(price) / target
    out["price_ratio"] = round(ratio, 4)
    negotiation_ratio = profile.budget.effective_purchase_negotiation_max / target
    out["price_negotiation_ratio"] = round(negotiation_ratio, 4)
    bands = (*PRICE_BANDS, (negotiation_ratio, PRICE_NEGOTIATION_POINTS))
    return band(ratio, bands)


def _land(prop: Property, profile: SearchProfile, out: dict[str, Any]) -> float:
    land_sqm = getattr(prop, "land_sqm", None)
    if not land_sqm:
        out["land_note"] = "plot size unknown - no land points awarded"
        out["land_multiple"] = None
        return 0.0
    multiple = float(land_sqm) / profile.land.preferred_min_sqm
    out["land_multiple"] = round(multiple, 3)
    out["land_meets_strong_minimum"] = float(land_sqm) >= profile.land.strong_min_sqm
    return band_below(multiple, LAND_BANDS, LAND_TOP_POINTS)


def _substance(prop: Property, out: dict[str, Any]) -> float:
    blob = text_blob(prop)
    points = 0.0
    matched: list[str] = []
    if (term := contains_any(blob, CORE_FARMSTEAD_TERMS)) is not None:
        points += SUBSTANCE_CORE_POINTS
        matched.append(term)
    year_built = getattr(prop, "year_built", None)
    historic_term = contains_any(blob, HISTORIC_TERMS)
    is_historic = (
        bool(getattr(prop, "is_monument", False))
        or historic_term is not None
        or (year_built is not None and year_built < HISTORIC_YEAR)
    )
    if is_historic:
        points += SUBSTANCE_HISTORIC_POINTS
        matched.append(historic_term or "historic substance")
    if (term := contains_any(blob, BARN_TERMS)) is not None:
        points += SUBSTANCE_BARN_POINTS
        matched.append(term)
    if (term := contains_any(blob, STABLE_TERMS)) is not None:
        points += SUBSTANCE_STABLE_POINTS
        matched.append(term)
    out["substance_matches"] = matched
    return min(points, SUBSTANCE_MAX)


def _seclusion(prop: Property, out: dict[str, Any]) -> float:
    blob = text_blob(prop)
    for terms, points, label in (
        (ALLEINLAGE_TERMS, ALLEINLAGE_POINTS, "alleinlage"),
        (ORTSRAND_TERMS, ORTSRAND_POINTS, "ortsrand"),
        (LOOSE_TERMS, LOOSE_POINTS, "loose development"),
    ):
        if (term := contains_any(blob, terms)) is not None:
            out["seclusion_match"] = term
            out["seclusion_class"] = label
            return points
    out["seclusion_match"] = None
    out["seclusion_class"] = "dense village (assumed - nothing stated)"
    return DENSE_POINTS


def _development(prop: Property, out: dict[str, Any]) -> float:
    """Provable > referenced > plausible. A broker adjective is never a proof."""
    blob = text_blob(prop)
    evidence: dict[str, Any] = getattr(prop, "evidence", None) or {}
    evidence_keys = {str(key).casefold() for key in evidence}

    if (term := contains_any(blob, PROVABLE_TERMS)) is not None:
        out["development_basis"] = f"provable: {term}"
        return DEVELOPMENT_PROVABLE_POINTS
    plausible_term = contains_any(blob, PLAUSIBLE_TERMS)
    corroborated = any(
        marker in key for key in evidence_keys for marker in DIVISIBILITY_EVIDENCE_KEYS
    )
    if plausible_term is not None and corroborated:
        out["development_basis"] = f"provable: {plausible_term} with documentary evidence"
        return DEVELOPMENT_PROVABLE_POINTS
    if (term := contains_any(blob, BAURECHT_TERMS)) is not None:
        out["development_basis"] = f"concrete Baurecht reference: {term}"
        return DEVELOPMENT_BAURECHT_POINTS
    if plausible_term is not None:
        out["development_basis"] = f"plausible only (seller wording): {plausible_term}"
        return DEVELOPMENT_PLAUSIBLE_POINTS
    out["development_basis"] = "unclear"
    return 0.0


def _outbuildings(prop: Property, out: dict[str, Any]) -> float:
    blob = text_blob(prop)
    present = [name for name, terms in OUTBUILDING_TYPES.items() if contains_any(blob, terms)]
    out["outbuilding_types"] = present
    return OUTBUILDING_POINTS.get(len(present), OUTBUILDING_MAX)


def fit_score(prop: Property, profile: SearchProfile) -> tuple[float, dict[str, Any]]:
    """Score 0-100 with a per-component breakdown the UI can render verbatim."""
    out: dict[str, Any] = {}

    geography = _geography(prop, profile, out)
    price = _price(prop, profile, out)
    land = _land(prop, profile, out)
    substance = _substance(prop, out)
    seclusion = _seclusion(prop, out)
    development = _development(prop, out)
    outbuildings = _outbuildings(prop, out)

    out.update(
        {
            "geography_score": geography,
            "geography_max": GEOGRAPHY_MAX,
            "price_score": price,
            "price_max": PRICE_MAX,
            "land_score": land,
            "land_max": LAND_MAX,
            "substance_score": substance,
            "substance_max": SUBSTANCE_MAX,
            "seclusion_score": seclusion,
            "seclusion_max": SECLUSION_MAX,
            #: Read by the exceptional-budget carve-out in deal.py / engine.py.
            "development_score": development,
            "development_max": DEVELOPMENT_MAX,
            "outbuildings_score": outbuildings,
            "outbuildings_max": OUTBUILDING_MAX,
        }
    )
    total = geography + price + land + substance + seclusion + development + outbuildings
    return round(total, 2), out
