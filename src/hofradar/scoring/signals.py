"""The three signals that are about the *listing*, not about the property.

``hidden_score``      how likely is it that nobody else has found this yet
``freshness_score``   how recently did the *seller* touch it (never: our crawler)
``confidence_score``  how much of the above do we actually believe

The freshness rule is the load-bearing one. A discovery source - a search
engine, an aggregator, a cache - proves only that a page existed somewhere at
some point. Letting its crawl timestamp raise freshness would make every stale
listing look new the moment we re-crawled it, which is exactly the failure mode
this project exists to avoid. Only ``Property.source_date`` and the
``last_verified`` of a source that is allowed to verify may raise freshness.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from hofradar.db.enums import ListingStatus, PriceType, SourceRole, VerificationStatus
from hofradar.scoring._util import (
    COMPLETENESS_FIELDS,
    band,
    best_source,
    contains_any,
    days_since,
    fold,
    now_utc,
    property_sources,
    text_blob,
    to_utc,
    verifying_sources,
)

if TYPE_CHECKING:  # pragma: no cover
    from hofradar.config import SearchProfile
    from hofradar.db.models import Property

# --------------------------------------------------------------------------- #
# hidden_score
# --------------------------------------------------------------------------- #

HIDDEN_MIN = 0.0
HIDDEN_MAX = 100.0

PRIVATE_SELLER_POINTS = 15.0
PRICE_NEGOTIABLE_POINTS = 8.0
PRICE_ON_REQUEST_POINTS = 8.0
LONG_ONLINE_POINTS = 10.0
PRICE_REDUCTIONS_POINTS = 12.0
POOR_PRESENTATION_POINTS = 5.0
SMALL_LOCAL_SOURCE_POINTS = 8.0
CHIFFRE_POINTS = 15.0
NO_BROKER_POINTS = 5.0
DIRECT_OWNER_CONTACT_POINTS = 10.0
FORECLOSURE_POINTS = 15.0
OFF_MARKET_POINTS = 20.0

#: Listed for longer than this: the market has looked and walked away, which is
#: an opportunity for a buyer with a different plan.
LONG_ONLINE_DAYS = 180
#: Listed for longer than *this*: the same fact is now a warning. The listing is
#: probably unmaintained, the price fictional, or the seller unreachable.
STALE_ONLINE_DAYS = 730
#: Applied to both hidden_score and freshness_score when STALE_ONLINE_DAYS is
#: passed, so the +10 "nobody found it" bonus cannot silently become a reward
#: for a dead listing.
STALE_PENALTY = -10.0
STALE_FLAG = "STALE_LISTING"

#: At least this many price cuts to count as "the seller is moving".
PRICE_REDUCTIONS_MIN = 2
#: A listing with no more images than this *and* a shorter description than
#: this was written by somebody who is not trying to reach the whole market.
#: Both conditions are required: we do not always download images, so a thin
#: image list on its own is our gap, not the seller's.
POOR_PRESENTATION_MAX_IMAGES = 2
POOR_PRESENTATION_MAX_DESCRIPTION = 400
#: A local source below this reliability is a village paper, not a portal.
SMALL_SOURCE_MAX_RELIABILITY = 0.75

CHIFFRE_TERMS: tuple[str, ...] = ("chiffre", "zuschriften unter", "chiffre nr")
NO_BROKER_TERMS: tuple[str, ...] = (
    "kein makler",
    "provisionsfrei",
    "ohne makler",
    "maklerfrei",
    "von privat",
    "privatverkauf",
)
BROKER_CONTACT_KIND = "broker"
PRIVATE_CONTACT_KIND = "private"

# --------------------------------------------------------------------------- #
# freshness_score
# --------------------------------------------------------------------------- #

#: (days since the best evidence-backed date, points).
FRESHNESS_BANDS: tuple[tuple[float, float], ...] = (
    (7, 100.0),
    (14, 90.0),
    (30, 75.0),
    (60, 50.0),
    (90, 30.0),
    (180, 10.0),
)
NO_EVIDENCE_DATE_FLAG = "NO_EVIDENCE_DATE"

# --------------------------------------------------------------------------- #
# confidence_score
# --------------------------------------------------------------------------- #

CONFIDENCE_WEIGHTS: dict[str, float] = {
    "source_reliability": 0.30,
    "availability": 0.20,
    "location": 0.20,
    "price": 0.15,
    "completeness": 0.10,
    "duplicate": 0.05,
}
#: How much of a source's stated reliability survives its role. A discovery
#: source can be perfectly reliable at what it does and still not prove much.
ROLE_FACTOR: dict[str, float] = {
    SourceRole.PRIMARY: 1.00,
    SourceRole.LOCAL: 0.85,
    SourceRole.DISCOVERY: 0.55,
}
#: (days since last_verified, points) for the availability term.
AVAILABILITY_BANDS: tuple[tuple[float, float], ...] = ((30, 100.0), (90, 60.0))
AVAILABILITY_STALE_POINTS = 25.0
AVAILABILITY_NEVER_POINTS = 0.0
GEO_PRECISION_POINTS: dict[str, float] = {
    "exact": 100.0,
    "street": 85.0,
    "town": 55.0,
    "postcode": 35.0,
    "none": 0.0,
}
PRICE_TYPE_POINTS: dict[str, float] = {
    PriceType.ASKING: 100.0,
    PriceType.NEGOTIABLE: 80.0,
    PriceType.AUCTION_MIN: 70.0,
    PriceType.ON_REQUEST: 35.0,
    PriceType.UNKNOWN: 0.0,
}
DUPLICATE_MULTI_SOURCE_POINTS = 100.0
DUPLICATE_SINGLE_SOURCE_POINTS = 70.0
DUPLICATE_NO_SOURCE_POINTS = 40.0
#: A merge somebody flagged for review is not settled, whatever the count says.
DUPLICATE_NEEDS_REVIEW_CEILING = 40.0
NEEDS_REVIEW_TERMS: tuple[str, ...] = ("needs_review", "needs review", "merge_review")

GONE_STATUSES: frozenset[str] = frozenset({ListingStatus.REMOVED, ListingStatus.SOLD})


def _online_days(prop: Property, now: datetime) -> float | None:
    """How long we have known about this listing at all.

    ``first_seen`` is our own bookkeeping, never evidence of seller activity, so
    it may drive the hidden-signal bonus and the stale penalty but it must never
    reach :func:`freshness_score`.
    """
    candidates = [
        days_since(getattr(prop, "first_seen", None), now),
        days_since(getattr(prop, "source_date", None), now),
    ]
    known = [value for value in candidates if value is not None]
    return max(known) if known else None


def hidden_score(
    prop: Property, profile: SearchProfile | None = None, now: datetime | None = None
) -> tuple[float, dict[str, Any]]:
    """Score 0-100 for "how likely is it that nobody else is bidding".

    ``profile`` is accepted for signature symmetry with the other components;
    the hidden signals are properties of the listing, not of the sliders.
    """
    moment = now_utc(now)
    blob = text_blob(prop)
    sources = property_sources(prop)
    signals: dict[str, float] = {}
    flags: list[str] = []

    if getattr(prop, "is_private_seller", False) or any(
        fold(ps.contact_kind) == PRIVATE_CONTACT_KIND for ps in sources
    ):
        signals["privatverkauf"] = PRIVATE_SELLER_POINTS
    price_type = fold(getattr(prop, "price_type", None))
    if price_type == PriceType.NEGOTIABLE:
        signals["preis_vb"] = PRICE_NEGOTIABLE_POINTS
    if price_type == PriceType.ON_REQUEST:
        signals["preis_auf_anfrage"] = PRICE_ON_REQUEST_POINTS

    online_days = _online_days(prop, moment)
    if online_days is not None and online_days >= LONG_ONLINE_DAYS:
        signals["long_online"] = LONG_ONLINE_POINTS
    is_stale = online_days is not None and online_days >= STALE_ONLINE_DAYS
    if is_stale:
        # Deliberately recorded as a separate, negative line item rather than
        # netted off silently: the UI has to be able to say both things.
        signals["stale_penalty"] = STALE_PENALTY
        flags.append(STALE_FLAG)

    if (getattr(prop, "price_reduction_count", 0) or 0) >= PRICE_REDUCTIONS_MIN:
        signals["price_reductions"] = PRICE_REDUCTIONS_POINTS

    images = getattr(prop, "images", None) or []
    description = getattr(prop, "description", None) or ""
    if (
        len(images) <= POOR_PRESENTATION_MAX_IMAGES
        and len(description) < POOR_PRESENTATION_MAX_DESCRIPTION
    ):
        signals["poor_presentation"] = POOR_PRESENTATION_POINTS

    if any(
        ps.source is not None
        and ps.source.role == SourceRole.LOCAL
        and (ps.source.reliability or 0.0) <= SMALL_SOURCE_MAX_RELIABILITY
        for ps in sources
    ):
        signals["small_local_source"] = SMALL_LOCAL_SOURCE_POINTS

    if contains_any(blob, CHIFFRE_TERMS) is not None:
        signals["chiffre"] = CHIFFRE_POINTS
    has_broker = any(fold(ps.contact_kind) == BROKER_CONTACT_KIND for ps in sources)
    has_named_contact = any(ps.contact_kind for ps in sources)
    if not has_broker and (
        contains_any(blob, NO_BROKER_TERMS) is not None or has_named_contact
    ):
        signals["kein_makler"] = NO_BROKER_POINTS
    if any(
        fold(ps.contact_kind) == PRIVATE_CONTACT_KIND and ps.contact_detail for ps in sources
    ):
        signals["direct_owner_contact"] = DIRECT_OWNER_CONTACT_POINTS

    if getattr(prop, "is_foreclosure", False):
        signals["zwangsversteigerung"] = FORECLOSURE_POINTS
    if getattr(prop, "is_off_market_signal", False):
        signals["off_market_hint"] = OFF_MARKET_POINTS

    raw = sum(signals.values())
    score = min(HIDDEN_MAX, max(HIDDEN_MIN, raw))
    out: dict[str, Any] = {
        "signals": signals,
        "raw_total": round(raw, 2),
        "online_days": round(online_days, 1) if online_days is not None else None,
        "is_stale": is_stale,
        "flags": flags,
    }
    return round(score, 2), out


def _evidence_dates(prop: Property) -> list[tuple[str, datetime]]:
    """Every date that a source we trust actually stands behind."""
    dates: list[tuple[str, datetime]] = []
    source_date = to_utc(getattr(prop, "source_date", None))
    if source_date is not None:
        dates.append(("property.source_date", source_date))
    verifying = verifying_sources(prop)
    if verifying:
        last_verified = to_utc(getattr(prop, "last_verified", None))
        if last_verified is not None and getattr(prop, "verification_status", None) in (
            VerificationStatus.VERIFIED,
            VerificationStatus.CONFLICTING,
        ):
            dates.append(("property.last_verified", last_verified))
        for ps in verifying:
            ps_date = to_utc(getattr(ps, "source_date", None))
            if ps_date is not None:
                dates.append((f"property_source[{ps.id}].source_date", ps_date))
    return dates


def freshness_score(prop: Property, now: datetime | None = None) -> tuple[float, dict[str, Any]]:
    """Score 0-100 from the age of the best *evidence-backed* date.

    Crawl timestamps (``last_seen``, ``Observation.scraped_at``, a discovery
    source's ``last_seen``) are ignored on purpose - see the module docstring.
    """
    moment = now_utc(now)
    dates = _evidence_dates(prop)
    out: dict[str, Any] = {
        "candidates": {label: value.isoformat() for label, value in dates},
        "flags": [],
    }
    if not dates:
        out["best_evidence_date"] = None
        out["days_old"] = None
        out["flags"].append(NO_EVIDENCE_DATE_FLAG)
        out["note"] = "no evidence-backed date; a crawl timestamp never raises freshness"
        return 0.0, out

    label, best = max(dates, key=lambda item: item[1])
    days_old = days_since(best, moment) or 0.0
    score = band(days_old, FRESHNESS_BANDS)
    out["best_evidence_date"] = best.isoformat()
    out["best_evidence_source"] = label
    out["days_old"] = round(days_old, 1)
    out["base_score"] = score

    online_days = _online_days(prop, moment)
    if online_days is not None and online_days >= STALE_ONLINE_DAYS:
        out["stale_penalty"] = STALE_PENALTY
        out["flags"].append(STALE_FLAG)
        score = max(0.0, score + STALE_PENALTY)
    return round(score, 2), out


def _availability_points(prop: Property, now: datetime) -> float:
    """Was this listing proved to be live, and how long ago?

    Unlike :func:`freshness_score`, this accepts the property-level
    ``verification_status`` as evidence in its own right - the lifecycle module
    only sets VERIFIED after fetching a primary source, and a property may carry
    that fact before its ``PropertySource`` rows have been rebuilt. What is not
    accepted, here as everywhere, is a discovery source: without either the
    verified flag or a verifying source, nothing has been proved.
    """
    if getattr(prop, "listing_status", None) in GONE_STATUSES:
        return 0.0
    days = days_since(getattr(prop, "last_verified", None), now)
    if days is None:
        return AVAILABILITY_NEVER_POINTS
    verified = fold(getattr(prop, "verification_status", None)) in (
        VerificationStatus.VERIFIED,
        VerificationStatus.CONFLICTING,
    )
    if not verified and not verifying_sources(prop):
        return AVAILABILITY_NEVER_POINTS
    return band(days, AVAILABILITY_BANDS, AVAILABILITY_STALE_POINTS)


def _duplicate_points(prop: Property) -> float:
    count = len(property_sources(prop))
    if count >= 2:
        points = DUPLICATE_MULTI_SOURCE_POINTS
    elif count == 1:
        points = DUPLICATE_SINGLE_SOURCE_POINTS
    else:
        points = DUPLICATE_NO_SOURCE_POINTS
    review_blob = " | ".join(
        [
            text_blob(prop),
            fold(getattr(prop, "user_state", None)),
            " ".join(str(risk) for risk in (getattr(prop, "llm_risks", None) or [])).casefold(),
        ]
    )
    if contains_any(review_blob, NEEDS_REVIEW_TERMS) is not None:
        points = min(points, DUPLICATE_NEEDS_REVIEW_CEILING)
    return points


def confidence_score(prop: Property, now: datetime | None = None) -> tuple[float, dict[str, Any]]:
    """Score 0-100: how much of what we just scored do we actually believe."""
    moment = now_utc(now)

    best = best_source(prop)
    if best is not None and best.source is not None:
        reliability = float(best.source.reliability or 0.0)
        factor = ROLE_FACTOR.get(best.source.role, ROLE_FACTOR[SourceRole.DISCOVERY])
        reliability_points = 100.0 * reliability * factor
        best_label = f"{best.source.key} ({best.source.role})"
    else:
        reliability_points = 0.0
        best_label = None

    availability_points = _availability_points(prop, moment)
    location_points = GEO_PRECISION_POINTS.get(fold(getattr(prop, "geo_precision", None)), 0.0)
    price_points = PRICE_TYPE_POINTS.get(fold(getattr(prop, "price_type", None)), 0.0)

    present = [field for field in COMPLETENESS_FIELDS if getattr(prop, field, None)]
    completeness_points = 100.0 * len(present) / len(COMPLETENESS_FIELDS)
    duplicate_points = _duplicate_points(prop)

    components = {
        "source_reliability": reliability_points,
        "availability": availability_points,
        "location": location_points,
        "price": price_points,
        "completeness": completeness_points,
        "duplicate": duplicate_points,
    }
    total = sum(CONFIDENCE_WEIGHTS[key] * value for key, value in components.items())
    out: dict[str, Any] = {
        "components": {key: round(value, 2) for key, value in components.items()},
        "weights": dict(CONFIDENCE_WEIGHTS),
        "weighted": {
            key: round(CONFIDENCE_WEIGHTS[key] * value, 2) for key, value in components.items()
        },
        "best_source": best_label,
        "fields_present": present,
        "source_count": len(property_sources(prop)),
    }
    return round(min(100.0, max(0.0, total)), 2), out
