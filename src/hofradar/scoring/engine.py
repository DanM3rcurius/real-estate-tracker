"""Putting the five components together, applying the gates, and caching.

The whole point of this file is the sentence in ``hofradar.db.models``: *scores
are not columns on Property*. The user drags the distance or the budget slider,
``profile.profile_hash`` changes, and every score is recomputed from unchanged
facts into a second, independent set of ``Score`` rows. Nothing about the
property is rewritten, no history is lost, and switching back to the old profile
finds the old scores still there.

``rescore_all`` is therefore on the hot path of the web UI: it answers a slider
move with three bulk queries for the whole database (properties with their
sources eagerly loaded, cost estimates, scores) and one commit. There is no
per-property lazy load and no N+1.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import selectinload

from hofradar.contracts import CostResult, ScoreResult
from hofradar.costmodel import estimate_costs
from hofradar.db.enums import HIDDEN_USER_STATES, CapitalRisk, ListingStatus, VerificationStatus
from hofradar.db.models import CostEstimate, Property, PropertySource, Score
from hofradar.scoring._util import to_utc
from hofradar.scoring.deal import deal_score
from hofradar.scoring.fit import fit_score
from hofradar.scoring.signals import confidence_score, freshness_score, hidden_score

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.orm import Session

    from hofradar.config import SearchProfile

# --------------------------------------------------------------------------- #
# Gate vocabulary. Every rejection is named, so the UI can explain it.
# --------------------------------------------------------------------------- #

REJECT_AIR_DISTANCE = "AIR_DISTANCE_OVER_LIMIT"
REJECT_DRIVING_DISTANCE = "DRIVING_DISTANCE_OVER_LIMIT"
REJECT_DRIVING_UNVERIFIED = "DRIVING_UNVERIFIED"
REJECT_PRICE = "PRICE_OVER_HARD_MAX"
REJECT_TOTAL_COST = "TOTAL_COST_OVER_HARD_MAX"
REJECT_EXCEPTIONAL_WITHOUT_DEVELOPMENT = "EXCEPTIONAL_BUDGET_WITHOUT_DEVELOPMENT"
REJECT_OBSERVATION_ONLY = "OBSERVATION_ONLY"
REJECT_LISTING_GONE = "LISTING_REMOVED_OR_SOLD"
REJECT_EXCLUDED_TYPE = "EXCLUDED_PROPERTY_TYPE"

FLAG_DRIVING_UNVERIFIED = "DRIVING_UNVERIFIED"
FLAG_SHORTLIST_BLOCKED = "SHORTLIST_BLOCKED"
FLAG_EXCEPTIONAL_CARVE_OUT = "EXCEPTIONAL_DEVELOPMENT_CARVE_OUT"

#: The total-cost gate was crossed only by a renovation figure nobody stated.
#: Recorded as a flag rather than a rejection: the pessimistic age rule is a
#: default, and defaulting a property out of the shortlist hides exactly the
#: pre-1960 farmsteads this project exists to find.
FLAG_COST_INFERRED = "TOTAL_COST_INFERRED"

#: A property whose road route has never been measured is held back rather than
#: silently passed: this ceiling sits between ``min_confidence_to_keep`` and
#: ``min_confidence_for_shortlist`` in the default profile, so such a property
#: stays in the database and out of the top ten until somebody routes it.
UNROUTED_CONFIDENCE_CEILING = 65.0

GONE_STATUSES: frozenset[str] = frozenset({ListingStatus.REMOVED, ListingStatus.SOLD})

#: An exclusion term ("Reihenhaus", "Neubau", "Eigentumswohnung") is a hard
#: reject, but only when nothing about the property contradicts it. A farm
#: advertising a planned "Neubau" of a Stadel is still a farm, so a listing
#: that carries real farmstead substance survives its exclusion terms and is
#: merely flagged for a human to look at.
FLAG_EXCLUSION_OVERRIDDEN = "EXCLUSION_OVERRIDDEN_BY_SUBSTANCE"

#: ``filters`` keys accepted by :func:`ranked_properties`. These are exactly the
#: names ``hofradar.web.deps.ResultFilters.as_scoring_filters`` emits: it sent
#: three this set had never heard of, ``_apply_filters`` raised, and the radar
#: silently fell back to unscored rows whenever those controls were touched.
#: ``tests/web/test_filter_contract.py`` keeps the two vocabularies equal.
SUPPORTED_FILTERS: frozenset[str] = frozenset(
    {
        "town",
        "min_land_sqm",
        "max_price",
        "status",
        "user_state",
        "verified_only",
        "has_outbuildings",
        "flags",
        "q",
    }
)

#: Fields a reader could plausibly type into the search box, in the order
#: :func:`matches_search` joins them into one haystack.
SEARCH_FIELDS: tuple[str, ...] = ("town", "postcode", "district", "canonical_title")


def matches_search(prop: Property, needle: str) -> bool:
    """Casefolded substring over the fields a reader would type (issue #14).

    Done in Python, not SQL: SQLite's ``lower()`` folds ASCII only, so a
    ``LIKE`` would miss every umlaut village - ``lower('Ödhof')`` never
    matches a casefolded ``öd``.
    """
    wanted = needle.casefold().strip()
    if not wanted:
        return True
    haystack = " ".join(str(getattr(prop, f, None) or "") for f in SEARCH_FIELDS).casefold()
    return wanted in haystack


# --------------------------------------------------------------------------- #
# Scoring one property
# --------------------------------------------------------------------------- #


def _has_farmstead_substance(prop: Property) -> bool:
    """Does anything about this property contradict its exclusion terms?

    Outbuildings are the strongest evidence - a Reihenhaus does not come with a
    Tenne. A recognised farmstead type name is the other.
    """
    if getattr(prop, "outbuildings", None):
        return True
    property_type = (getattr(prop, "property_type", None) or "").strip().lower()
    return bool(property_type) and property_type not in _EXCLUDED_TYPE_SLUGS


#: Types that are themselves an exclusion, so they cannot vouch for a property.
_EXCLUDED_TYPE_SLUGS: frozenset[str] = frozenset(
    {"eigentumswohnung", "doppelhaushaelfte", "reihenhaus", "neubau", "wohnung"}
)


def _apply_gates(
    prop: Property,
    profile: SearchProfile,
    cost: CostResult,
    result: ScoreResult,
    development_score: float,
) -> None:
    """Hard gates, applied after the weighted sum and before any ranking."""
    radius = profile.radius
    budget = profile.budget
    gates = profile.gates

    air = getattr(prop, "distance_air_km", None)
    if air is not None and air > radius.air_km_max:
        result.reject_reasons.append(REJECT_AIR_DISTANCE)

    driving = getattr(prop, "distance_driving_km", None)
    if driving is not None and driving > radius.effective_driving_hard:
        result.reject_reasons.append(REJECT_DRIVING_DISTANCE)
    elif driving is None and radius.require_driving_check:
        if gates.reject_unrouted:
            result.reject_reasons.append(REJECT_DRIVING_UNVERIFIED)
        elif FLAG_DRIVING_UNVERIFIED not in result.flags:
            result.flags.append(FLAG_DRIVING_UNVERIFIED)

    price = getattr(prop, "price", None)
    if price and price > budget.effective_purchase_hard_max:
        result.reject_reasons.append(REJECT_PRICE)

    total_mid = float(cost.total_mid or 0.0)
    carve_out = development_score >= gates.exceptional_development_min
    cost_is_inferred = cost.renovation_evidence != "observed"

    def _cost_reject(reason: str) -> None:
        """Reject only on a figure somebody stood behind; otherwise flag."""
        if cost_is_inferred:
            if FLAG_COST_INFERRED not in result.flags:
                result.flags.append(FLAG_COST_INFERRED)
        else:
            result.reject_reasons.append(reason)

    if total_mid > budget.effective_total_hard_max:
        if carve_out:
            result.flags.append(FLAG_EXCEPTIONAL_CARVE_OUT)
        else:
            _cost_reject(REJECT_TOTAL_COST)
    elif total_mid > budget.effective_total_exceptional_max:
        # The exceptional band: only genuinely exceptional development keeps it.
        if carve_out:
            result.flags.append(FLAG_EXCEPTIONAL_CARVE_OUT)
        else:
            _cost_reject(REJECT_EXCEPTIONAL_WITHOUT_DEVELOPMENT)

    if result.confidence_score < gates.min_confidence_to_keep:
        result.reject_reasons.append(REJECT_OBSERVATION_ONLY)

    if getattr(prop, "listing_status", None) in GONE_STATUSES and gates.reject_removed:
        result.reject_reasons.append(REJECT_LISTING_GONE)

    if getattr(prop, "exclusion_flags", None) and gates.reject_excluded:
        if _has_farmstead_substance(prop):
            result.flags.append(FLAG_EXCLUSION_OVERRIDDEN)
        else:
            result.reject_reasons.append(REJECT_EXCLUDED_TYPE)

    result.rejected = bool(result.reject_reasons)
    if result.confidence_score < gates.min_confidence_for_shortlist:
        result.flags.append(FLAG_SHORTLIST_BLOCKED)


def score_property(
    prop: Property,
    profile: SearchProfile,
    *,
    cost: CostResult | None = None,
    now: datetime | None = None,
) -> ScoreResult:
    """Score one property under one profile. Pure: nothing is written anywhere."""
    cost = cost if cost is not None else estimate_costs(prop, profile)

    fit, fit_bd = fit_score(prop, profile)
    deal, deal_bd = deal_score(prop, profile, cost)
    hidden, hidden_bd = hidden_score(prop, profile, now)
    freshness, freshness_bd = freshness_score(prop, now)
    confidence, confidence_bd = confidence_score(prop, now)

    flags: list[str] = []
    for source_bd in (deal_bd, hidden_bd, freshness_bd):
        for flag in source_bd.get("flags", ()):
            if flag not in flags:
                flags.append(flag)

    # An unmeasured road route caps how much we may believe the rest.
    driving_unknown = (
        getattr(prop, "distance_driving_km", None) is None and profile.radius.require_driving_check
    )
    if driving_unknown and confidence > UNROUTED_CONFIDENCE_CEILING:
        confidence_bd["uncapped_score"] = confidence
        confidence_bd["cap_reason"] = "no road route measured yet"
        confidence = UNROUTED_CONFIDENCE_CEILING

    weights = profile.weights
    final = (
        weights.fit * fit
        + weights.deal * deal
        + weights.hidden * hidden
        + weights.freshness * freshness
        + weights.confidence * confidence
    )

    result = ScoreResult(
        fit_score=fit,
        deal_score=deal,
        hidden_score=hidden,
        freshness_score=freshness,
        confidence_score=round(confidence, 2),
        final_score=round(final, 2),
        capital_risk=str(deal_bd.get("capital_risk", CapitalRisk.LOW)),
        flags=flags,
        breakdown={
            "fit": fit_bd,
            "deal": deal_bd,
            "hidden": hidden_bd,
            "freshness": freshness_bd,
            "confidence": confidence_bd,
            "weights": weights.model_dump(),
            "profile_hash": profile.profile_hash,
            "cost": {
                "total_low": cost.total_low,
                "total_mid": cost.total_mid,
                "total_high": cost.total_high,
                "renovation_mid": cost.renovation_mid,
                "renovation_tier": cost.renovation_tier,
            },
        },
    )
    _apply_gates(prop, profile, cost, result, float(fit_bd.get("development_score", 0.0)))
    return result


# --------------------------------------------------------------------------- #
# Caching a whole database of properties under one profile
# --------------------------------------------------------------------------- #


def _naive(value: datetime | None) -> datetime | None:
    """Comparable form of a timestamp regardless of how SQLite handed it back."""
    stamped = to_utc(value)
    return stamped.replace(tzinfo=None) if stamped is not None else None


def _is_dirty(prop: Property, score: Score | None, cost_row: CostEstimate | None) -> bool:
    if score is None or cost_row is None:
        return True
    scored_at = _naive(score.updated_at)
    changed_at = _naive(prop.updated_at)
    if scored_at is None or changed_at is None:
        return True
    return scored_at < changed_at


def _write_cost(
    session: Session, prop: Property, cost: CostResult, row: CostEstimate | None
) -> None:
    values = {
        "purchase_price": cost.purchase_price,
        "acquisition_costs": cost.acquisition_costs,
        "renovation_low": cost.renovation_low,
        "renovation_mid": cost.renovation_mid,
        "renovation_high": cost.renovation_high,
        "immediate_capex": cost.immediate_capex,
        "total_low": cost.total_low,
        "total_mid": cost.total_mid,
        "total_high": cost.total_high,
        "renovation_tier": cost.renovation_tier,
        "breakdown": cost.breakdown,
        "assumptions": cost.assumptions,
    }
    if row is None:
        session.add(CostEstimate(property_id=prop.id, **values))
        return
    for key, value in values.items():
        setattr(row, key, value)


def _write_score(
    session: Session, prop: Property, profile_hash: str, result: ScoreResult, row: Score | None
) -> None:
    values = {
        "fit_score": result.fit_score,
        "deal_score": result.deal_score,
        "hidden_score": result.hidden_score,
        "freshness_score": result.freshness_score,
        "confidence_score": result.confidence_score,
        "final_score": result.final_score,
        "capital_risk": result.capital_risk,
        "rejected": result.rejected,
        "reject_reasons": result.reject_reasons,
        "flags": result.flags,
        "breakdown": result.breakdown,
    }
    if row is None:
        session.add(Score(property_id=prop.id, profile_hash=profile_hash, **values))
        return
    for key, value in values.items():
        setattr(row, key, value)


def rescore_all(session: Session, profile: SearchProfile, *, only_dirty: bool = True) -> int:
    """Recompute ``CostEstimate`` and ``Score`` for every property. Idempotent.

    Returns the number of properties actually (re)scored. With
    ``only_dirty=True`` a property whose ``Score`` row for this profile is newer
    than the property itself is skipped, so calling this twice in a row after a
    slider move costs one query and returns 0 the second time.

    Score rows are keyed by ``profile.profile_hash``: writing the rows for a new
    profile never touches the rows of another one.
    """
    profile_hash = profile.profile_hash

    properties = (
        session.scalars(
            select(Property).options(
                selectinload(Property.property_sources).selectinload(PropertySource.source),
                selectinload(Property.images),
            )
        )
        .unique()
        .all()
    )
    # Cached rows are fetched as their own queries rather than through the
    # relationships: a Property already in the identity map keeps whatever its
    # relationship held when it was first loaded, which would make a second
    # call in the same session believe it still has no CostEstimate.
    costs = {row.property_id: row for row in session.scalars(select(CostEstimate))}
    scores = {
        row.property_id: row
        for row in session.scalars(select(Score).where(Score.profile_hash == profile_hash))
    }

    written = 0
    for prop in properties:
        cost_row = costs.get(prop.id)
        score_row = scores.get(prop.id)
        if only_dirty and not _is_dirty(prop, score_row, cost_row):
            continue
        cost = estimate_costs(prop, profile)
        result = score_property(prop, profile, cost=cost)
        _write_cost(session, prop, cost, cost_row)
        _write_score(session, prop, profile_hash, result, score_row)
        written += 1

    session.commit()
    return written


# --------------------------------------------------------------------------- #
# Ranking
# --------------------------------------------------------------------------- #


#: ``status`` value meaning "everything the market has not taken away". The
#: control panel offers it beside the real ``ListingStatus`` names, and matching
#: it literally against the column returned nothing at all.
STATUS_ALIVE = "alive"


def _apply_filters(stmt, filters: dict[str, Any]):
    unknown = set(filters) - SUPPORTED_FILTERS
    if unknown:
        raise ValueError(f"unsupported ranking filter(s): {sorted(unknown)}")
    if (town := filters.get("town")) is not None:
        towns = [town] if isinstance(town, str) else list(town)
        stmt = stmt.where(func.lower(Property.town).in_([t.casefold() for t in towns]))
    if (min_land := filters.get("min_land_sqm")) is not None:
        stmt = stmt.where(Property.land_sqm.is_not(None), Property.land_sqm >= min_land)
    if (max_price := filters.get("max_price")) is not None:
        stmt = stmt.where(Property.price.is_not(None), Property.price <= max_price)
    if (status := filters.get("status")) is not None:
        statuses = [status] if isinstance(status, str) else list(status)
        if STATUS_ALIVE in statuses:
            stmt = stmt.where(Property.listing_status.not_in(GONE_STATUSES))
            statuses = [s for s in statuses if s != STATUS_ALIVE]
        if statuses:
            stmt = stmt.where(Property.listing_status.in_(statuses))
    if (user_state := filters.get("user_state")) is not None:
        states = [user_state] if isinstance(user_state, str) else list(user_state)
        stmt = stmt.where(Property.user_state.in_(states))
    if filters.get("verified_only"):
        stmt = stmt.where(Property.verification_status == VerificationStatus.VERIFIED)
    return stmt


def ranked_properties(
    session: Session,
    profile: SearchProfile,
    *,
    limit: int | None = None,
    include_rejected: bool = False,
    include_hidden: bool = False,
    filters: dict[str, Any] | None = None,
) -> list[tuple[Property, Score]]:
    """Property + Score for this profile, best first.

    Ordering honours the shortlist gate: a property whose confidence is below
    ``gates.min_confidence_for_shortlist`` (the same rule that produced the
    ``SHORTLIST_BLOCKED`` flag) sorts behind every shortlistable one, so it can
    never occupy a place in the top ten.

    ``include_rejected`` and ``include_hidden`` are two different facts wearing
    one German word. The first is the machine's scoring gate (``Score.rejected``,
    recomputed for every ``profile_hash``); the second is the human's triage
    (``Property.user_state`` in :data:`~hofradar.db.enums.HIDDEN_USER_STATES`,
    which survives every re-run). A hidden property is still crawled, still
    rescored and still in the history - it is only kept off the reader's screen.
    """
    filters = dict(filters or {})
    wanted_flags = filters.pop("flags", None)
    wanted_outbuildings = filters.pop("has_outbuildings", None)
    wanted_search = filters.pop("q", None)

    blocked = case(
        (Score.confidence_score < profile.gates.min_confidence_for_shortlist, 1), else_=0
    )
    stmt = (
        select(Property, Score)
        .join(Score, Score.property_id == Property.id)
        .where(Score.profile_hash == profile.profile_hash)
        .order_by(blocked.asc(), Score.final_score.desc(), Property.id.asc())
    )
    if not include_rejected:
        stmt = stmt.where(Score.rejected.is_(False))
    if not include_hidden and filters.get("user_state") not in HIDDEN_USER_STATES:
        # Naming a hidden state in ``filters`` is an explicit ask to see it.
        # An untriaged property has no ``user_state`` at all, and NULL NOT IN
        # (...) is NULL in SQL, so the common case needs saying explicitly.
        stmt = stmt.where(
            or_(
                Property.user_state.is_(None),
                Property.user_state.not_in(HIDDEN_USER_STATES),
            )
        )
    stmt = _apply_filters(stmt, filters)

    rows: list[tuple[Property, Score]] = [tuple(row) for row in session.execute(stmt).all()]

    if wanted_search:
        # A casefolded Python substring, not a SQL LIKE - see matches_search.
        rows = [row for row in rows if matches_search(row[0], wanted_search)]

    if wanted_outbuildings:
        # A JSON list column; emptiness is not portably testable in SQL.
        rows = [row for row in rows if list(row[0].outbuildings or [])]

    if wanted_flags:
        # Flags live in a JSON column; filtering them in Python keeps the query
        # portable between SQLite and Postgres.
        needed = {wanted_flags} if isinstance(wanted_flags, str) else set(wanted_flags)
        rows = [row for row in rows if needed.issubset(set(row[1].flags or []))]

    return rows[:limit] if limit is not None else rows
