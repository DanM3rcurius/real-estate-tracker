"""The evidence model: is this listing the same physical place as that one?

The rule the whole design hangs on is that **no single soft signal may declare
a duplicate**. German farmstead listings are written by twelve different
brokers about the same yard and they never agree on wording; conversely three
genuinely different farms in one village share a town, a property type and a
suspiciously similar title. "Hofstelle in Vogtareuth", "Bauernhaus Vogtareuth"
and "Sacherl bei Vogtareuth" may be one object or three, and text alone can
never tell you which.

So ``compare`` scores a set of *independent* dimensions and demands that at
least :data:`MIN_CORROBORATING_DIMENSIONS` of the corroborating ones (geo,
land, living area, price, year, images) agree before it will say
``is_duplicate=True``. Title and description similarity can raise confidence
but can never, on their own, reach the threshold. When only location and type
line up and no number corroborates, the verdict carries a ``needs_review``
reason and a deliberately middling confidence: a human decides, the machine
does not guess.

Three things are treated as proof and short-circuit the model:

* the same ``external_id`` on the same source - that is literally the same
  listing seen twice;
* the same canonical listing URL, on *any* source - a URL names one page on
  one host, so two sources publishing it are publishing one listing, not two
  descriptions of one farm. This is the only proof here that crosses source
  boundaries, and it has to exist: a portal reached twice (a dedicated adapter
  and a syndicated feed of the same site) produces byte-identical URLs, and
  every other cross-source escape hatch is shut - ``external_id`` is one
  source's private numbering, and no adapter or normalizer populates
  ``image_hashes`` today, so the image proof below never fires either. Without
  it the same advert becomes two properties, which double-counts the
  shortlist, ``tracked_total`` and the per-source yield table. What counts as
  "the same URL" is deliberately narrow - see
  :func:`hofradar.dedupe._util.canonical_url`;
* a shared perceptual image hash - photographs of a farmyard are unique enough
  that one match at Hamming distance <= 6 is near-certainty.

All thresholds are module constants so they can be tuned from one place.
"""

from __future__ import annotations

from typing import Any

from rapidfuzz import fuzz

from hofradar.contracts import DuplicateVerdict
from hofradar.dedupe._facts import PRECISE_GEO, GeoLike, ListingFacts, facts_of
from hofradar.dedupe._util import fold_text, haversine_m, phash_hamming, relative_delta, slug

# --------------------------------------------------------------------------- #
# Tunable thresholds
# --------------------------------------------------------------------------- #

#: The blueprint rule: two precisely geocoded listings within 150 m with
#: similar land and living area are very likely the same object.
GEO_SAME_OBJECT_M = 150.0
#: Still the same hamlet - supporting, not decisive.
GEO_NEAR_M = 600.0
#: Beyond this, two precisely geocoded listings are different objects.
GEO_FAR_M = 3_000.0

#: Areas are quoted from different documents; +-5 % is the same building.
AREA_TOLERANCE = 0.05
#: Beyond this an area difference is a contradiction, not noise.
AREA_CONTRADICTION = 0.25

#: Prices differ by rounding and by portal; +-3 % is the same asking price.
PRICE_TOLERANCE = 0.03
#: A larger gap is weak evidence against, never proof - portals lag price cuts.
PRICE_CONTRADICTION = 0.20

#: rapidfuzz token_set_ratio bands on umlaut-folded, casefolded text.
TITLE_STRONG_RATIO = 88.0
TITLE_MODERATE_RATIO = 70.0
DESCRIPTION_STRONG_RATIO = 90.0

#: 64-bit phash: <= 6 differing bits is the same photograph.
PHASH_MAX_HAMMING = 6

#: Confidence at or above which we are willing to merge automatically.
DUPLICATE_THRESHOLD = 0.70
#: Confidence at or above which the pair is worth a human's attention.
NEEDS_REVIEW_THRESHOLD = 0.40
#: Confidence handed to a location+type-only match so it surfaces for review.
NEEDS_REVIEW_CONFIDENCE = 0.45
#: How many corroborating (non-textual) dimensions must agree to auto-merge.
MIN_CORROBORATING_DIMENSIONS = 2

# Dimension weights. They intentionally sum to more than 1.0; the total is
# clamped, so a pile of weak agreements cannot substitute for one strong one.
W_GEO_SAME = 0.30
W_GEO_NEAR = 0.12
W_LAND = 0.20
W_LIVING = 0.18
W_PRICE = 0.14
W_YEAR_EXACT = 0.10
W_YEAR_DECADE = 0.04
W_TITLE_STRONG = 0.10
W_TITLE_MODERATE = 0.04
W_DESCRIPTION_STRONG = 0.12
W_LOCATION = 0.12
W_TYPE = 0.05
W_STREET = 0.10

P_LAND_MISMATCH = -0.20
P_LIVING_MISMATCH = -0.18
P_PRICE_MISMATCH = -0.05
P_YEAR_MISMATCH = -0.15
P_GEO_FAR = -0.60
P_LOCATION_MISMATCH = -0.10

#: Confidence assigned by the two proof rules.
PROOF_CONFIDENCE = 1.0
IMAGE_PROOF_CONFIDENCE = 0.97

#: Dimensions that count as independent corroboration of physical identity.
CORROBORATING_DIMENSIONS = frozenset({"geo", "land", "living", "price", "year", "image"})


def compare(a: Any, b: Any, *, a_geo: GeoLike = None, b_geo: GeoLike = None) -> DuplicateVerdict:
    """Score two listings/properties for identity.

    ``a`` and ``b`` may each be a ``NormalizedListing`` or a ``Property``.
    ``a_geo`` / ``b_geo`` are optional and only used to supply coordinates for
    a listing that has not been geocoded into a row yet; they are additive to
    the documented ``compare(a, b)`` contract, never required.
    """
    return compare_facts(facts_of(a, geo=a_geo), facts_of(b, geo=b_geo))


def compare_facts(fa: ListingFacts, fb: ListingFacts) -> DuplicateVerdict:
    """The scoring core. Works purely on projected facts."""
    matched_id = fb.property_id or fa.property_id

    shared_ids = fa.source_ids & fb.source_ids
    if shared_ids:
        key, ext = sorted(shared_ids)[0]
        return DuplicateVerdict(
            is_duplicate=True,
            confidence=PROOF_CONFIDENCE,
            reasons=[f"same_external_id: {key}:{ext} (proof)"],
            matched_property_id=matched_id,
        )

    shared_urls = fa.canonical_urls & fb.canonical_urls
    if shared_urls:
        return DuplicateVerdict(
            is_duplicate=True,
            confidence=PROOF_CONFIDENCE,
            reasons=[f"same_canonical_url: {sorted(shared_urls)[0]} (proof)"],
            matched_property_id=matched_id,
        )

    image_distance = _best_image_distance(fa, fb)
    if image_distance is not None and image_distance <= PHASH_MAX_HAMMING:
        return DuplicateVerdict(
            is_duplicate=True,
            confidence=IMAGE_PROOF_CONFIDENCE,
            reasons=[f"shared_image: phash hamming distance {image_distance} (near-proof)"],
            matched_property_id=matched_id,
        )

    score = 0.0
    reasons: list[str] = []
    agreed: set[str] = set()
    vetoed = False

    # -- geography ---------------------------------------------------------- #
    distance_m = _distance_m(fa, fb)
    precise = fa.geo_precision in PRECISE_GEO and fb.geo_precision in PRECISE_GEO
    if distance_m is None:
        reasons.append("geo_unknown: no comparable coordinates")
    elif not precise:
        reasons.append(
            f"geo_coarse: {distance_m:.0f} m apart but precision is "
            f"{fa.geo_precision}/{fb.geo_precision} - not usable as identity evidence"
        )
    elif distance_m <= GEO_SAME_OBJECT_M:
        score += W_GEO_SAME
        agreed.add("geo")
        reasons.append(f"geo_same_object: {distance_m:.0f} m apart (<= {GEO_SAME_OBJECT_M:.0f} m)")
    elif distance_m <= GEO_NEAR_M:
        score += W_GEO_NEAR
        reasons.append(f"geo_near: {distance_m:.0f} m apart")
    elif distance_m > GEO_FAR_M:
        score += P_GEO_FAR
        vetoed = True
        reasons.append(f"geo_far: {distance_m / 1000:.1f} km apart - different objects")
    else:
        reasons.append(f"geo_apart: {distance_m:.0f} m apart")

    # -- location text ------------------------------------------------------ #
    location_agree = _location_agreement(fa, fb, reasons)
    if location_agree is True:
        score += W_LOCATION
    elif location_agree is False:
        score += P_LOCATION_MISMATCH

    if fa.street and fb.street and slug(fa.street) == slug(fb.street):
        score += W_STREET
        reasons.append(f"street_match: {fa.street}")

    # -- areas -------------------------------------------------------------- #
    score += _numeric_dimension(
        "land", fa.land_sqm, fb.land_sqm, AREA_TOLERANCE, AREA_CONTRADICTION,
        W_LAND, P_LAND_MISMATCH, "m2", agreed, reasons,
    )
    score += _numeric_dimension(
        "living", fa.living_sqm, fb.living_sqm, AREA_TOLERANCE, AREA_CONTRADICTION,
        W_LIVING, P_LIVING_MISMATCH, "m2", agreed, reasons,
    )

    # -- price -------------------------------------------------------------- #
    # "One side has no price" is neutral. Silence is not disagreement.
    score += _numeric_dimension(
        "price", fa.price, fb.price, PRICE_TOLERANCE, PRICE_CONTRADICTION,
        W_PRICE, P_PRICE_MISMATCH, "EUR", agreed, reasons, contradiction_vetoes=False,
    )

    # -- year built --------------------------------------------------------- #
    if fa.year_built is not None and fb.year_built is not None:
        if fa.year_built == fb.year_built:
            score += W_YEAR_EXACT
            agreed.add("year")
            reasons.append(f"year_exact: {fa.year_built}")
        elif fa.year_built // 10 == fb.year_built // 10:
            score += W_YEAR_DECADE
            reasons.append(f"year_decade: {fa.year_built} vs {fb.year_built}")
        else:
            score += P_YEAR_MISMATCH
            reasons.append(f"year_mismatch: {fa.year_built} vs {fb.year_built}")
    else:
        reasons.append("year_unknown")

    # -- type --------------------------------------------------------------- #
    type_agree = bool(
        fa.property_type and fb.property_type and slug(fa.property_type) == slug(fb.property_type)
    )
    if type_agree:
        score += W_TYPE
        reasons.append(f"type_match: {fa.property_type}")

    # -- text (never decisive on its own) ----------------------------------- #
    title_ratio = _ratio(fa.title, fb.title)
    if title_ratio is not None:
        if title_ratio >= TITLE_STRONG_RATIO:
            score += W_TITLE_STRONG
            reasons.append(f"title_similar: token_set_ratio {title_ratio:.0f}")
        elif title_ratio >= TITLE_MODERATE_RATIO:
            score += W_TITLE_MODERATE
            reasons.append(f"title_partial: token_set_ratio {title_ratio:.0f}")
        else:
            reasons.append(f"title_different: token_set_ratio {title_ratio:.0f}")

    description_ratio = _ratio(fa.description, fb.description)
    if description_ratio is not None and description_ratio >= DESCRIPTION_STRONG_RATIO:
        score += W_DESCRIPTION_STRONG
        reasons.append(f"description_similar: token_set_ratio {description_ratio:.0f}")

    # -- verdict ------------------------------------------------------------ #
    confidence = max(0.0, min(1.0, score))
    corroborating = agreed & CORROBORATING_DIMENSIONS
    is_duplicate = (
        not vetoed
        and confidence >= DUPLICATE_THRESHOLD
        and len(corroborating) >= MIN_CORROBORATING_DIMENSIONS
    )

    if corroborating:
        joined = ", ".join(sorted(corroborating))
        reasons.append(f"corroborating_dimensions: {len(corroborating)} ({joined})")
    else:
        reasons.append("corroborating_dimensions: 0")

    if not is_duplicate and not corroborating and not vetoed:
        # Same village, same kind of building, similar words - and not one
        # number to back it up. This is the Vogtareuth case: it may be one farm
        # or three, and only a human can tell.
        if location_agree and (type_agree or (title_ratio or 0) >= TITLE_MODERATE_RATIO):
            confidence = max(confidence, NEEDS_REVIEW_CONFIDENCE)
            reasons.append(
                "needs_review: location and type agree but no numeric field corroborates"
            )
    elif not is_duplicate and confidence >= NEEDS_REVIEW_THRESHOLD and not vetoed:
        reasons.append("needs_review: partial agreement below the merge threshold")

    return DuplicateVerdict(
        is_duplicate=is_duplicate,
        confidence=round(confidence, 4),
        reasons=reasons,
        matched_property_id=matched_id,
    )


# --------------------------------------------------------------------------- #
# Dimension helpers
# --------------------------------------------------------------------------- #


def _numeric_dimension(
    name: str,
    a: float | None,
    b: float | None,
    tolerance: float,
    contradiction: float,
    weight: float,
    penalty: float,
    unit: str,
    agreed: set[str],
    reasons: list[str],
    *,
    contradiction_vetoes: bool = True,
) -> float:
    """Score one numeric field. A missing value is neutral, never negative."""
    delta = relative_delta(a, b)
    if delta is None:
        reasons.append(f"{name}_unknown: one side has no value (neutral)")
        return 0.0
    if delta <= tolerance:
        agreed.add(name)
        reasons.append(f"{name}_match: {a:.0f} vs {b:.0f} {unit} ({delta * 100:.1f} %)")
        return weight
    if contradiction_vetoes and delta >= contradiction:
        reasons.append(f"{name}_contradiction: {a:.0f} vs {b:.0f} {unit} ({delta * 100:.1f} %)")
        return penalty * 1.5
    reasons.append(f"{name}_mismatch: {a:.0f} vs {b:.0f} {unit} ({delta * 100:.1f} %)")
    return penalty


def _location_agreement(fa: ListingFacts, fb: ListingFacts, reasons: list[str]) -> bool | None:
    if fa.postcode and fb.postcode:
        if fa.postcode.strip() == fb.postcode.strip():
            reasons.append(f"postcode_match: {fa.postcode}")
            return True
        reasons.append(f"postcode_mismatch: {fa.postcode} vs {fb.postcode}")
        return False
    if fa.town and fb.town:
        if slug(fa.town) == slug(fb.town):
            reasons.append(f"town_match: {fa.town}")
            return True
        reasons.append(f"town_mismatch: {fa.town} vs {fb.town}")
        return False
    reasons.append("location_unknown")
    return None


def _distance_m(fa: ListingFacts, fb: ListingFacts) -> float | None:
    ca, cb = fa.coords, fb.coords
    if ca is None or cb is None:
        return None
    return haversine_m(ca, cb)


def _best_image_distance(fa: ListingFacts, fb: ListingFacts) -> int | None:
    best: int | None = None
    for ha in fa.image_hashes:
        for hb in fb.image_hashes:
            d = phash_hamming(ha, hb)
            if d is not None and (best is None or d < best):
                best = d
    return best


def _ratio(a: str | None, b: str | None) -> float | None:
    """rapidfuzz token_set_ratio over umlaut-folded, casefolded text."""
    fa, fb = fold_text(a), fold_text(b)
    if not fa or not fb:
        return None
    return float(fuzz.token_set_ratio(fa, fb))
