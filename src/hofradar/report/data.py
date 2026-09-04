"""The weekly digest, as data.

The blueprint asks for a *briefing*, not a dump: a handful of counts, then at
most ``profile.gates.shortlist_size`` properties worth a phone call, and every
other thing that happened reduced to a number. Restraint is the feature - a
report that lists eighty farms is a report nobody reads on a Sunday morning.

One rule is absolute and is enforced twice here (once when classifying, once as
a final sweep in :func:`_enforce_new_rule`): a property that has ever been in
the database is NEVER listed as NEU. It reappears as REAKTIVIERT, as a price
change, or simply as known. Announcing the same farm as new every week is the
exact failure mode this project exists to avoid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from hofradar.config import SearchProfile
from hofradar.db.enums import ChangeKind, ListingStatus, VerificationStatus
from hofradar.db.models import Property, Score
from hofradar.report.yield_stats import (
    MunicipalityCoverage,
    SourceYield,
    coverage_by_municipality,
    source_yield,
)
from hofradar.web import history, lazy
from hofradar.web.filters import de_eur, de_km, de_pct, de_sqm, week_label

#: How far back a weekly report looks when the caller does not say.
DEFAULT_PERIOD_DAYS = 7

#: How far back the per-source yield table looks. Wider than the weekly window
#: on purpose - a source's worth is judged over its first several runs, not
#: one week's trickle, and the go/no-go in docs/DECISIONS.md entry 14 is
#: itself phrased as "across its first four weekly runs".
YIELD_WINDOW_DAYS = 28

ACTION_CALL = "📞 SOFORT"
ACTION_WATCH = "👀 BEOBACHTEN"
ACTION_CHECK = "📄 PRÜFEN"

CATEGORY_LABELS = {
    "new": "🆕 NEU",
    "reactivated": "♻️ REAKTIVIERT",
    "price_change": "🔻 PREISÄNDERUNG",
    "known": "♻️ BEKANNT",
}

STATUS_LABELS = {
    ListingStatus.DISCOVERED: "entdeckt",
    ListingStatus.VERIFIED: "verifiziert",
    ListingStatus.ACTIVE: "aktiv",
    ListingStatus.PRICE_CHANGED: "Preis geändert",
    ListingStatus.STALE: "veraltet",
    ListingStatus.REMOVED: "entfernt",
    ListingStatus.EXPIRED: "Anzeige abgelaufen",
    ListingStatus.SOLD: "verkauft",
    ListingStatus.FORECLOSURE: "Zwangsversteigerung",
    ListingStatus.OFF_MARKET_SIGNAL: "Off-Market-Signal",
}


@dataclass(slots=True)
class ReportCounts:
    """Everything that happened, as numbers. Most of it is never listed."""

    tracked_total: int = 0
    active_total: int = 0
    newly_verified: int = 0
    new_candidates: int = 0
    reactivated: int = 0
    price_changes: int = 0
    removed: int = 0
    foreclosures: int = 0
    off_market_signals: int = 0
    known_updated: int = 0
    shortlisted: int = 0
    not_listed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "tracked_total": self.tracked_total,
            "active_total": self.active_total,
            "newly_verified": self.newly_verified,
            "new_candidates": self.new_candidates,
            "reactivated": self.reactivated,
            "price_changes": self.price_changes,
            "removed": self.removed,
            "foreclosures": self.foreclosures,
            "off_market_signals": self.off_market_signals,
            "known_updated": self.known_updated,
            "shortlisted": self.shortlisted,
            "not_listed": self.not_listed,
        }


@dataclass(slots=True)
class ReportEntry:
    """One shortlisted property, already phrased for a human."""

    rank: int
    public_id: str
    title: str
    town: str | None
    price: float | None
    price_type: str
    land_sqm: float | None
    living_sqm: float | None
    distance_air_km: float | None
    distance_driving_km: float | None

    fit_score: float
    deal_score: float
    hidden_score: float
    freshness_score: float
    confidence_score: float
    final_score: float

    why: list[str]
    risks: list[str]
    total_low: float | None
    total_mid: float | None
    total_high: float | None
    status: str
    status_label: str
    is_primary_source: bool
    action: str
    category: str
    url: str | None

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS.get(self.category, self.category)

    @property
    def driving_display(self) -> str:
        """Never borrows the air distance. Unknown stays unknown."""
        if self.distance_driving_km is None:
            return "nicht geprüft"
        return de_km(self.distance_driving_km)


@dataclass(slots=True)
class ReportData:
    week_label: str
    generated_at: datetime
    period_start: date
    since: datetime
    profile_name: str
    profile_hash: str
    radius_air_km: float
    radius_driving_soft_km: float
    radius_driving_hard_km: float
    budget_total_max: float
    budget_purchase_target: float
    budget_purchase_hard: float
    counts: ReportCounts
    entries: list[ReportEntry] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: Per-source in-radius yield since ``since`` - see ``hofradar.report.yield_stats``.
    #: A source can parse flawlessly and still produce nothing worth ranking; this is
    #: how that becomes visible instead of discovered five weeks later.
    source_yields: list[SourceYield] = field(default_factory=list)
    #: Per-municipality observation counts over the same window as
    #: :attr:`source_yields`, for every town ``config/search.yaml`` names as
    #: expected - see ``hofradar.report.yield_stats.coverage_by_municipality``.
    #: A town with ``observed == 0`` is a dark municipality: the report must
    #: say so by name, because silence alone cannot distinguish a quiet market
    #: from an uncovered one.
    municipality_coverage: list[MunicipalityCoverage] = field(default_factory=list)
    #: How many days :attr:`source_yields` covers. Carried on the data rather than
    #: baked into the renderers' heading text, so ``YIELD_WINDOW_DAYS`` has exactly
    #: one place to change.
    yield_window_days: int = YIELD_WINDOW_DAYS
    run_id: int | None = None
    center_name: str = ""

    @property
    def yield_window_weeks(self) -> int:
        return self.yield_window_days // 7

    def summary(self) -> dict[str, Any]:
        """The JSON blob persisted on :class:`~hofradar.db.models.ReportRecord`."""
        return {
            "week_label": self.week_label,
            "generated_at": self.generated_at.isoformat(),
            "profile_hash": self.profile_hash,
            "radius_air_km": self.radius_air_km,
            "budget_total_max": self.budget_total_max,
            "counts": self.counts.as_dict(),
            "shortlist": [e.public_id for e in self.entries],
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #


#: Statuses whose reactivation is a billing event rather than news about the
#: farmstead, and so never headlines the digest. An advert on a fixed paid
#: window (``listing_ttl_days``) expires and renews on a fortnightly timer;
#: counting each renewal as REAKTIVIERT is precisely the metronome
#: docs/DECISIONS.md entry 15 exists to keep out of a ten-entry digest. The
#: reappearance is still in the append-only history under its own
#: ChangeKind.REACTIVATED row - this is a reporting judgement, not an erasure.
#: Nothing genuinely newsworthy is dropped: an advert down long enough for its
#: return to mean something has already been moved on to STALE by
#: ``apply_stale_rules``, and a reactivation out of STALE does count.
_UNREPORTED_REACTIVATION_ORIGINS: frozenset[str] = frozenset({ListingStatus.EXPIRED})


def _was_reactivated(prop: Property, since: datetime) -> bool:
    for row in history.status_events_since(prop, since):
        if getattr(row, "change_kind", "") != ChangeKind.REACTIVATED:
            continue
        if getattr(row, "old_status", None) in _UNREPORTED_REACTIVATION_ORIGINS:
            continue
        return True
    return False


def categorise(prop: Property, since: datetime) -> str:
    """``new`` | ``reactivated`` | ``price_change`` | ``known``.

    ``new`` requires that the database holds *no* evidence of this place from
    before ``since`` - not merely that ``first_seen`` looks recent.
    """
    if _was_reactivated(prop, since):
        return "reactivated"
    if history.is_genuinely_new(prop, since):
        return "new"
    if history.price_events_since(prop, since):
        return "price_change"
    return "known"


#: Scoring flags are warnings by default. Only these read as a reason to be
#: interested; everything else is routed to the risk list instead, so "Warum
#: interessant" never argues for a property with the reasons not to buy it.
POSITIVE_FLAG_LABELS: dict[str, str] = {
    "EXCEPTIONAL_DEVELOPMENT_CARVE_OUT": "Belegtes Entwicklungspotenzial trägt das Budget",
    "EXCLUSION_OVERRIDDEN_BY_SUBSTANCE": "Trotz Ausschlussbegriff echte Hofsubstanz",
}

#: Warning flags that the risk list does not already state in its own words.
#: DRIVING_UNVERIFIED and SHORTLIST_BLOCKED are deliberately absent - _risks
#: derives both from the underlying facts, and saying it twice reads as two
#: separate problems.
WARNING_FLAG_LABELS: dict[str, str] = {
    "SANIERUNGSRISIKO": "Sanierungskosten übersteigen den Kaufpreis deutlich",
}


def _why(prop: Property, score: Score | None, profile: SearchProfile) -> list[str]:
    reasons: list[str] = []
    if prop.land_sqm:
        if prop.land_sqm >= profile.land.strong_min_sqm:
            reasons.append(f"Sehr großes Grundstück ({de_sqm(prop.land_sqm)})")
        elif prop.land_sqm >= profile.land.preferred_min_sqm:
            reasons.append(f"Grundstück über Zielgröße ({de_sqm(prop.land_sqm)})")
    if prop.outbuildings:
        reasons.append("Nebengebäude: " + ", ".join(prop.outbuildings[:4]))
    delta = history.total_price_delta_pct(prop)
    if delta is not None and delta <= -3:
        reasons.append(f"Preis seit Ersterfassung {de_pct(delta)}")
    if prop.is_private_seller:
        reasons.append("Privatverkauf – kein Maklerhonorar")
    if prop.is_off_market_signal:
        reasons.append("Off-Market-Signal, noch nicht auf den Portalen")
    if prop.distance_air_km is not None and prop.distance_air_km <= profile.radius.air_km_max * 0.5:
        reasons.append(f"Nah am Suchzentrum ({de_km(prop.distance_air_km)} Luftlinie)")
    if prop.special_features:
        reasons.append("Besonderheiten: " + ", ".join(prop.special_features[:3]))
    if score is not None:
        for label in score.flags or []:
            phrase = POSITIVE_FLAG_LABELS.get(str(label))
            if phrase:
                reasons.append(phrase)
    if not reasons and prop.price is not None:
        reasons.append(f"Im Budget ({de_eur(prop.price)})")
    return reasons[:5]


def _risks(prop: Property, score: Score | None, cost: Any, profile: SearchProfile) -> list[str]:
    risks: list[str] = []
    if score is not None:
        risks.extend(str(r) for r in (score.reject_reasons or []))
        for label in score.flags or []:
            phrase = WARNING_FLAG_LABELS.get(str(label))
            if phrase:
                risks.append(phrase)
        if score.capital_risk and score.capital_risk != "low":
            risks.append(f"Kapitalrisiko: {score.capital_risk}")
        if score.confidence_score < profile.gates.min_confidence_for_shortlist:
            risks.append(f"Konfidenz nur {score.confidence_score:.0f} – Fakten ungeprüft")
    if prop.verification_status != VerificationStatus.VERIFIED:
        risks.append(f"Verifikation: {prop.verification_status}")
    if prop.distance_driving_km is None:
        risks.append("Fahrstrecke nicht geprüft")
    if prop.geo_precision in ("none", "postcode", "town"):
        risks.append(f"Standort nur auf {prop.geo_precision}-Ebene bekannt")
    if prop.is_monument:
        risks.append("Denkmalschutz – Auflagen und Kosten prüfen")
    if prop.is_foreclosure:
        risks.append("Zwangsversteigerung – Besichtigung oft nicht möglich")
    if prop.exclusion_flags:
        risks.append("Ausschlussmerkmale: " + ", ".join(prop.exclusion_flags[:3]))
    if cost is not None and cost.total_high and cost.total_high > profile.budget.effective_total_hard_max:
        risks.append(f"Oberes Kostenband {de_eur(cost.total_high)} über Budgetgrenze")
    if cost is not None and cost.renovation_tier in ("heavy", "complete"):
        risks.append(f"Sanierungsstufe {cost.renovation_tier}")
    if not risks:
        risks.append("Keine harten Risiken erkannt – trotzdem vor Ort prüfen")
    return risks[:6]


def _action(prop: Property, score: Score | None, profile: SearchProfile) -> str:
    confidence = score.confidence_score if score is not None else 0.0
    final = score.final_score if score is not None else 0.0
    unverified = prop.verification_status != VerificationStatus.VERIFIED
    if unverified or confidence < profile.gates.min_confidence_for_shortlist:
        return ACTION_CHECK
    if final >= 75 or prop.is_off_market_signal:
        return ACTION_CALL
    return ACTION_WATCH


def _best_url(prop: Property) -> tuple[str | None, bool]:
    primary = False
    url = None
    for row in prop.property_sources or []:
        if row.is_best or url is None:
            url = row.url
        if row.is_primary_source:
            primary = True
            if row.is_best or url is None:
                url = row.url
    return url, primary


# --------------------------------------------------------------------------- #
# Counting
# --------------------------------------------------------------------------- #


def _load_properties(session: Session) -> list[Property]:
    stmt = (
        select(Property)
        .where(Property.merged_into_id.is_(None))
        .options(
            selectinload(Property.scores),
            selectinload(Property.price_history),
            selectinload(Property.status_history),
            selectinload(Property.property_sources),
            selectinload(Property.observations),
            selectinload(Property.cost_estimate),
        )
    )
    return list(session.scalars(stmt).unique())


def _count(properties: list[Property], since: datetime) -> ReportCounts:
    counts = ReportCounts(tracked_total=len(properties))
    for prop in properties:
        if prop.is_alive:
            counts.active_total += 1
        category = categorise(prop, since)
        if category == "new":
            counts.new_candidates += 1
        elif category == "reactivated":
            counts.reactivated += 1
        if history.price_events_since(prop, since):
            counts.price_changes += 1
        verified_at = history.as_aware(prop.last_verified)
        if verified_at is not None and verified_at >= since:
            counts.newly_verified += 1
        removed_at = history.as_aware(prop.removed_at)
        if (removed_at is not None and removed_at >= since) or any(
            getattr(row, "new_status", "") in (ListingStatus.REMOVED, ListingStatus.SOLD)
            for row in history.status_events_since(prop, since)
        ):
            counts.removed += 1
        if prop.is_foreclosure or prop.listing_status == ListingStatus.FORECLOSURE:
            counts.foreclosures += 1
        if prop.is_off_market_signal or prop.listing_status == ListingStatus.OFF_MARKET_SIGNAL:
            counts.off_market_signals += 1
        last_seen = history.as_aware(prop.last_seen)
        if category != "new" and last_seen is not None and last_seen >= since:
            counts.known_updated += 1
    return counts


# --------------------------------------------------------------------------- #
# Ranking (lazily, so a half-written scoring module cannot break the report)
# --------------------------------------------------------------------------- #


def _ranked(
    session: Session, profile: SearchProfile, properties: list[Property], limit: int
) -> tuple[list[tuple[Property, Score | None]], list[str]]:
    notes: list[str] = []
    lazy.call_or("hofradar.scoring:rescore_all", None, session, profile)
    pairs, degraded = lazy.call_or(
        "hofradar.scoring:ranked_properties",
        None,
        session,
        profile,
        limit=limit,
        include_rejected=False,
    )
    if degraded is not None or pairs is None:
        if degraded is not None:
            notes.append(degraded.message)
        scored: list[tuple[Property, Score | None]] = []
        for prop in properties:
            score = next(
                (s for s in (prop.scores or []) if s.profile_hash == profile.profile_hash), None
            )
            if score is not None and score.rejected:
                continue
            scored.append((prop, score))
        scored.sort(
            key=lambda row: (
                -(row[1].final_score if row[1] is not None else -1.0),
                -(history.as_aware(row[0].first_seen) or datetime.now(UTC)).timestamp(),
            )
        )
        return scored[:limit], notes

    normalised: list[tuple[Property, Score | None]] = []
    for item in pairs:
        prop, score = (item[0], item[1]) if isinstance(item, tuple) else (item, None)
        if score is None:
            score = next(
                (s for s in (prop.scores or []) if s.profile_hash == profile.profile_hash), None
            )
        normalised.append((prop, score))
    return normalised[:limit], notes


def _enforce_new_rule(entries: list[ReportEntry], properties_by_id: dict[str, Property],
                      since: datetime, notes: list[str]) -> None:
    """Final sweep. If anything still claims NEU without earning it, demote it.

    Belt and braces on top of :func:`categorise`, because this is the one rule
    the product must never break and a future change to the classifier must not
    be able to break it silently.
    """
    for entry in entries:
        if entry.category != "new":
            continue
        prop = properties_by_id.get(entry.public_id)
        if prop is None or not history.is_genuinely_new(prop, since):
            entry.category = "known"
            notes.append(
                f"{entry.public_id}: als bekannt eingestuft – Belege reichen vor den Berichtszeitraum zurück."
            )


def build_report(
    session: Session,
    profile: SearchProfile,
    *,
    run_id: int | None = None,
    since: datetime | None = None,
    now: datetime | None = None,
) -> ReportData:
    """Assemble the weekly digest for ``profile``."""
    now = now or datetime.now(UTC)
    since = history.as_aware(since) or (now - timedelta(days=DEFAULT_PERIOD_DAYS))

    properties = _load_properties(session)
    counts = _count(properties, since)

    shortlist_size = max(0, int(profile.gates.shortlist_size))
    ranked, notes = _ranked(session, profile, properties, shortlist_size)

    entries: list[ReportEntry] = []
    for rank, (prop, score) in enumerate(ranked, start=1):
        cost = prop.cost_estimate
        url, is_primary = _best_url(prop)
        entries.append(
            ReportEntry(
                rank=rank,
                public_id=prop.public_id,
                title=prop.canonical_title,
                town=prop.town,
                price=prop.price,
                price_type=prop.price_type,
                land_sqm=prop.land_sqm,
                living_sqm=prop.living_sqm,
                distance_air_km=prop.distance_air_km,
                distance_driving_km=prop.distance_driving_km,
                fit_score=score.fit_score if score else 0.0,
                deal_score=score.deal_score if score else 0.0,
                hidden_score=score.hidden_score if score else 0.0,
                freshness_score=score.freshness_score if score else 0.0,
                confidence_score=score.confidence_score if score else 0.0,
                final_score=score.final_score if score else 0.0,
                why=_why(prop, score, profile),
                risks=_risks(prop, score, cost, profile),
                total_low=cost.total_low if cost else None,
                total_mid=cost.total_mid if cost else None,
                total_high=cost.total_high if cost else None,
                status=prop.listing_status,
                status_label=STATUS_LABELS.get(prop.listing_status, prop.listing_status),
                is_primary_source=is_primary,
                action=_action(prop, score, profile),
                category=categorise(prop, since),
                url=url,
            )
        )

    _enforce_new_rule(entries, {p.public_id: p for p in properties}, since, notes)

    counts.shortlisted = len(entries)
    counts.not_listed = max(0, counts.tracked_total - counts.shortlisted)

    yield_since = now - timedelta(days=YIELD_WINDOW_DAYS)
    yields = source_yield(session, since=yield_since, radius_air_km=profile.radius.air_km_max)
    coverage = coverage_by_municipality(
        session, since=yield_since, expected=profile.coverage.municipalities
    )

    return ReportData(
        week_label=week_label(now),
        generated_at=now,
        period_start=since.date(),
        since=since,
        profile_name=profile.name,
        profile_hash=profile.profile_hash,
        radius_air_km=profile.radius.air_km_max,
        radius_driving_soft_km=profile.radius.effective_driving_soft,
        radius_driving_hard_km=profile.radius.effective_driving_hard,
        budget_total_max=profile.budget.total_budget_max,
        budget_purchase_target=profile.budget.effective_purchase_target_max,
        budget_purchase_hard=profile.budget.effective_purchase_hard_max,
        counts=counts,
        entries=entries,
        notes=notes,
        source_yields=yields,
        municipality_coverage=coverage,
        yield_window_days=YIELD_WINDOW_DAYS,
        run_id=run_id,
        center_name=profile.center.name,
    )


def total_property_count(session: Session) -> int:
    return session.scalar(select(func.count(Property.id))) or 0
