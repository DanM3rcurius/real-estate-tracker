"""Turning the two sliders into a ranked list of farms.

This is the request path behind ``GET /`` and ``GET /api/results``: rescore
under the current profile, ask ``hofradar.scoring`` for the ranking, then
*re-apply the slider limits ourselves*. That last step is deliberate. The web
layer is the thing the user points at when they say "I set 40 km and it shows
me 90 km", so it never delegates the final say on what appears to another
package - and it keeps working when ``hofradar.scoring`` is not importable yet.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from hofradar.config import SearchProfile
from hofradar.db.enums import HIDDEN_USER_STATES, ListingStatus, VerificationStatus
from hofradar.db.models import CostEstimate, Property, Score
from hofradar.web import history, lazy
from hofradar.web.deps import ResultFilters


@dataclass(slots=True)
class Chip:
    """A small coloured badge on a result card. ``label`` already carries its emoji."""

    label: str
    kind: str
    title: str = ""


@dataclass(slots=True)
class ResultRow:
    rank: int
    prop: Property
    score: Score | None
    cost: CostEstimate | None
    chips: list[Chip]
    best_url: str | None
    price_delta_pct: float | None


@dataclass(slots=True)
class ResultSet:
    profile: SearchProfile
    filters: ResultFilters
    rows: list[ResultRow]
    profile_hash: str
    rescored: int | None
    total_matched: int
    total_in_db: int
    #: Held back by the machine's scoring gate (``Score.rejected``).
    hidden_rejected: int
    #: Held back by the human's triage (``Property.user_state``). Two different
    #: facts, so two counters - the status line names both rather than letting
    #: a property vanish without a number saying it did.
    hidden_archived: int = 0
    degraded: list[lazy.Degraded] = field(default_factory=list)

    @property
    def status_line(self) -> str:
        """The little "system is re-thinking" line under the sliders."""
        rescored = "–" if self.rescored is None else str(self.rescored)
        line = (
            f"Profil {self.profile_hash} · {rescored} Objekte neu bewertet · "
            f"{self.total_matched} von {self.total_in_db} im Filter"
        )
        if self.hidden_archived:
            line += f" · {self.hidden_archived} archivierte ausgeblendet"
        return line


# --------------------------------------------------------------------------- #
# Chips
# --------------------------------------------------------------------------- #


def change_chips(prop: Property, *, now: datetime | None = None, window_days: int = 7) -> list[Chip]:
    """The change vocabulary from the blueprint, in a fixed, scannable order."""
    now = now or history.now_utc()
    since = history.window_start(window_days, now=now)
    chips: list[Chip] = []

    if prop.listing_status in (ListingStatus.REMOVED, ListingStatus.SOLD):
        chips.append(Chip("❌ NICHT MEHR AKTIV", "gone", "Inserat nachweislich verschwunden"))

    if history.is_genuinely_new(prop, since):
        chips.append(Chip("🆕 NEU", "new", "Erstmals in dieser Woche erfasst"))
    else:
        chips.append(Chip("♻️ BEKANNT", "known", "War vor dieser Woche schon in der Datenbank"))

    if history.price_events_since(prop, since) or (prop.price_reduction_count or 0) > 0:
        chips.append(Chip("🔻 PREISÄNDERUNG", "price", "Preis hat sich seit der Erfassung geändert"))

    if prop.is_off_market_signal or prop.listing_status == ListingStatus.OFF_MARKET_SIGNAL:
        chips.append(Chip("🟣 OFF-MARKET", "offmarket", "Hinweis außerhalb der Portale"))

    if prop.is_foreclosure or prop.listing_status == ListingStatus.FORECLOSURE:
        chips.append(Chip("⚖️ ZWANGSVERSTEIGERUNG", "zvg", "Zwangsversteigerungsverfahren"))

    if prop.is_monument:
        chips.append(Chip("🏛 DENKMAL", "monument", "Denkmalschutz – Auflagen prüfen"))

    unclear: list[str] = []
    if prop.verification_status != VerificationStatus.VERIFIED:
        unclear.append(f"Verifikation: {prop.verification_status}")
    if prop.price is None:
        unclear.append("kein Preis bekannt")
    if prop.geo_precision in ("none", "postcode", "town"):
        unclear.append(f"Standort nur {prop.geo_precision}")
    if unclear:
        chips.append(Chip("⚠️ UNGEKLÄRT", "unclear", "; ".join(unclear)))

    return chips


def best_url(prop: Property) -> str | None:
    sources = list(prop.property_sources or [])
    if not sources:
        return None
    for row in sources:
        if row.is_best:
            return row.url
    for row in sources:
        if row.is_primary_source:
            return row.url
    return sources[0].url


# --------------------------------------------------------------------------- #
# Filtering - the web layer's own, non-negotiable pass
# --------------------------------------------------------------------------- #


def passes_profile(prop: Property, cost: CostEstimate | None, profile: SearchProfile) -> bool:
    """Do the two headline sliders admit this property?

    Unknown values pass: a farm with no measured distance is a question, not a
    rejection, and hiding it would quietly shrink the search.
    """
    air = prop.distance_air_km
    if air is not None and air > profile.radius.air_km_max:
        return False
    price = prop.price
    if price is not None and price > profile.budget.effective_purchase_hard_max:
        return False
    if cost is not None and cost.total_mid is not None:
        if cost.total_mid > profile.budget.effective_total_hard_max:
            return False
    return True


def is_hidden(prop: Property, filters: ResultFilters) -> bool:
    """Did triage take this property off the screen?

    Not the same question as ``Score.rejected``: this one is the human's, it
    survives every re-run and every profile change, and it changes nothing
    about what the crawler and the scorer keep doing with the property.
    """
    if filters.include_hidden:
        return False
    return (prop.user_state or "") in HIDDEN_USER_STATES


def passes_filters(prop: Property, filters: ResultFilters) -> bool:
    if is_hidden(prop, filters):
        return False
    if filters.min_land_sqm:
        if prop.land_sqm is None or prop.land_sqm < filters.min_land_sqm:
            return False
    if filters.status == "alive":
        if not prop.is_alive:
            return False
    elif filters.status and prop.listing_status != filters.status:
        return False
    if filters.verified_only and prop.verification_status != VerificationStatus.VERIFIED:
        return False
    if filters.outbuildings_only and not (prop.outbuildings or []):
        return False
    if filters.user_state and (prop.user_state or "none") != filters.user_state:
        return False
    if filters.town:
        needle = filters.town.casefold()
        haystack = " ".join(
            str(x or "") for x in (prop.town, prop.postcode, prop.district, prop.canonical_title)
        ).casefold()
        if needle not in haystack:
            return False
    return True


def _sort_key(row: tuple[Property, Score | None], sort: str) -> Any:
    prop, score = row
    if sort == "price":
        return (prop.price is None, prop.price or 0.0)
    if sort == "distance":
        return (prop.distance_air_km is None, prop.distance_air_km or 0.0)
    if sort == "newest":
        first = history.as_aware(prop.first_seen) or history.now_utc()
        return (-first.timestamp(),)
    if sort == "drop":
        delta = history.total_price_delta_pct(prop)
        return (delta is None, delta if delta is not None else 0.0)
    final = score.final_score if score is not None else -1.0
    return (-final, prop.id)


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #


def _eager(stmt):
    return stmt.options(
        selectinload(Property.scores),
        selectinload(Property.price_history),
        selectinload(Property.status_history),
        selectinload(Property.property_sources),
        selectinload(Property.cost_estimate),
    )


def _fallback_pairs(
    session: Session, profile: SearchProfile, *, include_rejected: bool, include_hidden: bool
) -> list[tuple[Property, Score | None]]:
    """Used when ``hofradar.scoring`` is unavailable.

    Shows the facts we do have rather than an error page: unscored properties
    still have a town, a price and a distance, and that is already useful. A
    degraded radar still honours triage - losing the scorer is no reason to put
    an archived property back on the screen.
    """
    stmt = _eager(select(Property).where(Property.merged_into_id.is_(None)))
    if not include_hidden:
        stmt = stmt.where(
            (Property.user_state.is_(None)) | (Property.user_state.not_in(HIDDEN_USER_STATES))
        )
    props = list(session.scalars(stmt).unique())
    pairs: list[tuple[Property, Score | None]] = []
    for prop in props:
        score = next(
            (s for s in (prop.scores or []) if s.profile_hash == profile.profile_hash), None
        )
        if score is not None and score.rejected and not include_rejected:
            continue
        pairs.append((prop, score))
    return pairs


def _rescore(session: Session, profile: SearchProfile) -> tuple[int | None, lazy.Degraded | None]:
    count, degraded = lazy.call_or("hofradar.scoring:rescore_all", None, session, profile)
    if degraded is not None:
        return None, degraded
    # A brand-new profile hash has no cached rows at all; if the incremental
    # pass found nothing, force a full recompute so the sliders really bite.
    if not count:
        missing = session.scalar(
            select(func.count(Property.id)).where(
                ~Property.scores.any(Score.profile_hash == profile.profile_hash)
            )
        )
        if missing:
            forced, forced_degraded = lazy.call_or(
                "hofradar.scoring:rescore_all", None, session, profile, only_dirty=False
            )
            if forced_degraded is None and forced:
                return int(forced), None
    return int(count or 0), None


def build_results(
    session: Session,
    profile: SearchProfile,
    filters: ResultFilters,
    *,
    now: datetime | None = None,
) -> ResultSet:
    """The one function ``/`` and ``/api/results`` both call."""
    degraded: list[lazy.Degraded] = []
    now = now or history.now_utc()

    total_in_db = session.scalar(select(func.count(Property.id))) or 0

    rescored, rescore_degraded = _rescore(session, profile)
    if rescore_degraded is not None:
        degraded.append(rescore_degraded)

    pairs, rank_degraded = lazy.call_or(
        "hofradar.scoring:ranked_properties",
        None,
        session,
        profile,
        include_rejected=filters.include_rejected,
        include_hidden=filters.include_hidden,
        filters=filters.as_scoring_filters(),
    )
    if rank_degraded is not None:
        if rescore_degraded is None or rank_degraded.message != rescore_degraded.message:
            degraded.append(rank_degraded)
        pairs = None
    if pairs is None:
        pairs = _fallback_pairs(
            session,
            profile,
            include_rejected=filters.include_rejected,
            include_hidden=filters.include_hidden,
        )

    normalised: list[tuple[Property, Score | None]] = []
    for item in pairs:
        if isinstance(item, tuple):
            prop, score = item[0], (item[1] if len(item) > 1 else None)
        else:  # a scoring build that yields bare Property rows
            prop, score = item, None
        if score is None:
            score = next(
                (s for s in (prop.scores or []) if s.profile_hash == profile.profile_hash), None
            )
        normalised.append((prop, score))

    # Counted with its own query rather than in the loop below: both the ranking
    # and the degraded fallback drop archived rows before they ever get here, and
    # a hidden property with no number beside it is the failure mode this
    # codebase keeps producing.
    hidden_archived = 0
    if not filters.include_hidden:
        hidden_archived = (
            session.scalar(
                select(func.count(Property.id)).where(
                    Property.merged_into_id.is_(None),
                    Property.user_state.in_(HIDDEN_USER_STATES),
                )
            )
            or 0
        )

    hidden_rejected = 0
    kept: list[tuple[Property, Score | None]] = []
    for prop, score in normalised:
        if prop.merged_into_id is not None:
            continue
        cost = prop.cost_estimate
        if not passes_profile(prop, cost, profile) or not passes_filters(prop, filters):
            continue
        if score is not None and score.rejected and not filters.include_rejected:
            hidden_rejected += 1
            continue
        kept.append((prop, score))

    kept.sort(key=lambda row: _sort_key(row, filters.sort))
    total_matched = len(kept)
    kept = kept[: filters.limit]

    rows = [
        ResultRow(
            rank=index,
            prop=prop,
            score=score,
            cost=prop.cost_estimate,
            chips=change_chips(prop, now=now),
            best_url=best_url(prop),
            price_delta_pct=history.total_price_delta_pct(prop),
        )
        for index, (prop, score) in enumerate(kept, start=1)
    ]

    return ResultSet(
        profile=profile,
        filters=filters,
        rows=rows,
        profile_hash=profile.profile_hash,
        rescored=rescored,
        total_matched=total_matched,
        total_in_db=total_in_db,
        hidden_rejected=hidden_rejected,
        hidden_archived=hidden_archived,
        degraded=degraded,
    )


def load_property(session: Session, public_id: str) -> Property | None:
    stmt = _eager(select(Property).where(Property.public_id == public_id)).options(
        selectinload(Property.observations),
        selectinload(Property.images),
        selectinload(Property.documents),
    )
    return session.scalars(stmt).unique().one_or_none()


def row_to_dict(row: ResultRow) -> dict[str, Any]:
    """JSON shape for ``/api/properties.json`` and the map."""
    prop, score, cost = row.prop, row.score, row.cost
    return {
        "rank": row.rank,
        "public_id": prop.public_id,
        "title": prop.canonical_title,
        "town": prop.town,
        "postcode": prop.postcode,
        "lat": prop.lat,
        "lon": prop.lon,
        "geo_precision": prop.geo_precision,
        "distance_air_km": prop.distance_air_km,
        "distance_driving_km": prop.distance_driving_km,
        "distance_driving_checked": prop.distance_driving_km is not None,
        "price": prop.price,
        "price_type": prop.price_type,
        "price_delta_pct": row.price_delta_pct,
        "land_sqm": prop.land_sqm,
        "living_sqm": prop.living_sqm,
        "property_type": prop.property_type,
        "listing_status": prop.listing_status,
        "verification_status": prop.verification_status,
        "user_state": prop.user_state,
        "url": row.best_url,
        "chips": [c.label for c in row.chips],
        "scores": None
        if score is None
        else {
            "fit": score.fit_score,
            "deal": score.deal_score,
            "hidden": score.hidden_score,
            "freshness": score.freshness_score,
            "confidence": score.confidence_score,
            "final": score.final_score,
            "capital_risk": score.capital_risk,
            "rejected": score.rejected,
            "reject_reasons": list(score.reject_reasons or []),
        },
        "cost": None
        if cost is None
        else {
            "total_low": cost.total_low,
            "total_mid": cost.total_mid,
            "total_high": cost.total_high,
            "renovation_tier": cost.renovation_tier,
        },
    }
