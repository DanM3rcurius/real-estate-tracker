"""Renovation tier inference.

Why this module is deliberately pessimistic
===========================================

A farmstead advertised as "nur wenig Renovierungsstau" is, once the roof is
opened, routinely a full ``kernsanierung``. Every euro that this module
underestimates becomes a euro the buyer discovers after the notary appointment,
so the rule everywhere below is: **when the evidence is thin, assume the worse
tier**. An unknown condition on a pre-1960 building is HEAVY, never MEDIUM.

The tier is a fact about the building, not about the user's sliders, which is
why it lives here and not in :mod:`hofradar.scoring` - it is cached once per
property in ``CostEstimate`` and survives every slider move.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from hofradar.costmodel._text import contains_any, fold, fold_all
from hofradar.db.enums import RenovationTier

if TYPE_CHECKING:  # pragma: no cover - import cycle only matters for type checkers
    from hofradar.db.models import Property

#: Built before this, assume pre-war substance: no insulation, no damp course,
#: single glazing, and electrics that predate the current DIN standards.
PRE_MODERN_YEAR = 1960
#: Built before this, assume the 1970s-80s technical fit-out is due anyway.
MODERN_YEAR = 1995

#: Tags that prove the substance is gone. Note ``kernsanierung`` (the noun: work
#: still to be done) is NOT ``kernsaniert`` (the participle: work already done).
COMPLETE_TAGS: tuple[str, ...] = (
    "abrissreif",
    "abbruchreif",
    "entkernt",
    "kernsanierung",
    "ruine",
    "unbewohnbar",
    "rohbau",
)
#: Tags that prove major structural or technical work is outstanding.
HEAVY_TAGS: tuple[str, ...] = (
    "sanierungsbeduerftig",
    "sanierungsbedarf",
    "handwerkerobjekt",
    "unsaniert",
    "nicht saniert",
    "grundlegend zu sanieren",
    "feuchtigkeitsschaden",
    "hausschwamm",
)
#: Tags that promise cosmetic work only. On old substance this is still bumped
#: up a tier by :data:`PRE_MODERN_YEAR`.
MEDIUM_TAGS: tuple[str, ...] = (
    "renovierungsbeduerftig",
    "renovierungsbedarf",
    "modernisierungsbedarf",
    "instandsetzungsbedarf",
)
#: Tags that prove work has already happened.
LIGHT_TAGS: tuple[str, ...] = (
    "kernsaniert",
    "vollsaniert",
    "saniert",
    "neuwertig",
    "modernisiert",
    "renoviert",
    "bezugsfertig",
    "erstbezug nach sanierung",
)

#: ``Property.condition`` free-text / ConditionVerdict values mapped to a tier.
CONDITION_TIERS: dict[str, RenovationTier] = {
    "good": RenovationTier.LIGHT,
    "gut": RenovationTier.LIGHT,
    "gepflegt": RenovationTier.LIGHT,
    "neuwertig": RenovationTier.LIGHT,
    "saniert": RenovationTier.LIGHT,
    "fair": RenovationTier.MEDIUM,
    "mittel": RenovationTier.MEDIUM,
    "durchschnittlich": RenovationTier.MEDIUM,
    "bad": RenovationTier.HEAVY,
    "schlecht": RenovationTier.HEAVY,
    "desolat": RenovationTier.HEAVY,
    "abbruch": RenovationTier.COMPLETE,
}
#: Conditions that carry no information - they must fall through to the age rule.
UNINFORMATIVE_CONDITIONS: frozenset[str] = frozenset({"", "unknown", "unbekannt", "conflicting"})

#: Ordering used when several signals disagree: the worst one wins.
_TIER_SEVERITY: dict[RenovationTier, int] = {
    RenovationTier.UNKNOWN: -1,
    RenovationTier.LIGHT: 0,
    RenovationTier.MEDIUM: 1,
    RenovationTier.HEAVY: 2,
    RenovationTier.COMPLETE: 3,
}
_BUMPED_BY_AGE: dict[RenovationTier, RenovationTier] = {
    RenovationTier.LIGHT: RenovationTier.MEDIUM,
    RenovationTier.MEDIUM: RenovationTier.HEAVY,
}


def property_tags(prop: Property) -> list[str]:
    """Every canonical tag attached to a property, folded and de-duplicated."""
    tags: list[str] = []
    for attr in ("building_features", "outbuildings", "special_features", "exclusion_flags"):
        tags.extend(fold_all(getattr(prop, attr, None)))
    condition = fold(getattr(prop, "condition", None))
    if condition:
        tags.append(condition)
    return list(dict.fromkeys(tags))


def _tier_from_tags(tags: list[str]) -> RenovationTier:
    """Worst tier implied by the tag list. Checked worst-first so that
    ``unsaniert`` can never be matched by the ``saniert`` substring."""
    blob = " ".join(tags)
    if contains_any(blob, COMPLETE_TAGS):
        return RenovationTier.COMPLETE
    if contains_any(blob, HEAVY_TAGS):
        return RenovationTier.HEAVY
    if contains_any(blob, MEDIUM_TAGS):
        return RenovationTier.MEDIUM
    if contains_any(blob, LIGHT_TAGS):
        return RenovationTier.LIGHT
    return RenovationTier.UNKNOWN


def _tier_from_condition(prop: Property) -> RenovationTier:
    condition = fold(getattr(prop, "condition", None))
    if condition in UNINFORMATIVE_CONDITIONS:
        return RenovationTier.UNKNOWN
    for key, tier in CONDITION_TIERS.items():
        if key in condition:
            return tier
    return RenovationTier.UNKNOWN


def _tier_from_age(year_built: int | None) -> RenovationTier:
    """The fallback when nobody said anything about the condition.

    No stated condition is not good news on a farmstead: it usually means the
    listing is a three-line classified for a building nobody has maintained.
    """
    if year_built is None or year_built < PRE_MODERN_YEAR:
        return RenovationTier.HEAVY
    if year_built < MODERN_YEAR:
        return RenovationTier.MEDIUM
    return RenovationTier.LIGHT


#: What the renovation tier rests on. ``observed`` means the listing said
#: something about the condition; ``inferred`` means we fell through to the age
#: rule, which is a deliberately pessimistic default rather than a measurement.
EVIDENCE_OBSERVED = "observed"
EVIDENCE_INFERRED = "inferred"


def renovation_evidence(prop: Property) -> str:
    """Did anybody actually state this building's condition?

    Kept separate from :func:`infer_renovation_tier` so the tier stays a single
    value with one meaning. Callers that must not act on a guess ask this.
    """
    if _tier_from_condition(prop) is not RenovationTier.UNKNOWN:
        return EVIDENCE_OBSERVED
    if _tier_from_tags(property_tags(prop)) is not RenovationTier.UNKNOWN:
        return EVIDENCE_OBSERVED
    return EVIDENCE_INFERRED


def infer_renovation_tier(prop: Property) -> RenovationTier:
    """Infer the renovation tier from condition, year of construction and tags.

    Resolution order:

    1. the worst tier implied by any tag or by ``condition`` wins;
    2. if nothing was said at all, the age of the building decides, and an
       unknown or pre-1960 year yields HEAVY (never MEDIUM);
    3. a stated light/medium tier on pre-1960 substance is bumped one tier,
       because "renoviert" on a 1890 Hofstelle means new paint, not new joists.
    """
    stated = max(
        (_tier_from_tags(property_tags(prop)), _tier_from_condition(prop)),
        key=lambda tier: _TIER_SEVERITY[tier],
    )
    year_built: int | None = getattr(prop, "year_built", None)
    if stated is RenovationTier.UNKNOWN:
        return _tier_from_age(year_built)
    if year_built is not None and year_built < PRE_MODERN_YEAR:
        return _BUMPED_BY_AGE.get(stated, stated)
    return stated
