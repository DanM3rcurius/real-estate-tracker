"""The only writer of ``Property`` rows.

Everything the system claims to remember passes through :func:`ingest`, and it
is built around four non-negotiable invariants.

**1. The observation is written first.** ``Observation`` is append-only. It is
inserted before any canonical fact is touched, so the raw record of what a
source said on a given day survives even if every rule below is later found to
be wrong. N ingests produce N observation rows; nothing is ever updated.

**2. FIRST_SEEN is structurally impossible for a known property.** The kind is
assigned in exactly one branch - the one that just created the row - and a
final guard re-checks it. A property that was REMOVED and shows up again is
REACTIVATED. If its price also moved on that same run the answer is
PRICE_CHANGE, and *both* transitions are written to ``status_history``, because
the weekly report must be able to say "it is back, and it is cheaper".

**3. Not every source may assert every fact.** A verifying source (primary or
local) may overwrite a fact. A discovery source may only fill a hole, and may
never set ``last_verified``, ``verification_status=VERIFIED`` or
``source_date``: an aggregator's crawl date is not evidence of freshness.

**4. Nothing is overwritten with NULL.** A crawl that failed to parse the land
area has not proven the farm has no land.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from hofradar.contracts import ChangeResult, GeoResult, NormalizedListing
from hofradar.db.enums import ChangeKind, ListingStatus, SourceRole, VerificationStatus
from hofradar.db.models import (
    Image,
    Observation,
    PriceHistory,
    Property,
    PropertySource,
    Source,
    StatusHistory,
    VerificationEvent,
    utcnow,
)
from hofradar.dedupe import fingerprint, find_duplicate
from hofradar.lifecycle import _rules

#: Smallest price movement that counts as a change rather than rounding noise.
PRICE_CHANGE_MIN_ABS = 1.0

#: Scalar facts copied straight off the listing under the write rules.
_FACT_FIELDS = (
    ("description", "description"),
    ("street", "street"),
    ("postcode", "postcode"),
    ("town", "town"),
    ("district", "district"),
    ("land_sqm", "land_sqm"),
    ("living_sqm", "living_sqm"),
    ("usable_sqm", "usable_sqm"),
    ("rooms", "rooms"),
    ("year_built", "year_built"),
    ("property_type", "property_type"),
)

_TAG_FIELDS = (
    ("building_features", "building_features"),
    ("outbuildings", "outbuildings"),
    ("special_features", "special_features"),
    ("exclusion_flags", "exclusion_flags"),
)

_FLAG_FIELDS = ("is_foreclosure", "is_monument", "is_private_seller", "is_off_market_signal")


@dataclass(slots=True)
class _Before:
    """The property as it stood before this observation was applied."""

    status: str
    price: float | None
    text_hash: str | None
    known: bool


def ingest(
    session: Session,
    listing: NormalizedListing,
    *,
    run_id: int | None = None,
    source: Source,
    geo: GeoResult | None = None,
) -> tuple[Property, ChangeResult]:
    """Fold one normalised listing into the property table."""
    now = utcnow()

    verdict = find_duplicate(session, listing, geo=geo)
    prop: Property | None = None
    if verdict.is_duplicate and verdict.matched_property_id is not None:
        prop = session.get(Property, verdict.matched_property_id)
        while prop is not None and prop.merged_into_id is not None:
            prop = session.get(Property, prop.merged_into_id)

    is_new = prop is None
    if prop is None:
        prop = _create_bare_property(session, listing, now)

    before = _Before(
        status=prop.listing_status,
        price=prop.price,
        text_hash=_last_text_hash(session, prop.id, source.id),
        known=not is_new,
    )

    # (1) append-only record, written before a single canonical fact moves.
    _record_observation(session, prop, listing, source=source, run_id=run_id, now=now)

    _apply_facts(prop, listing, source=source, geo=geo, now=now)
    price_change = _apply_price(session, prop, listing, source=source, before=before, now=now)
    ps, source_row_created = _sync_property_source(session, prop, listing, source=source, now=now)
    _sync_images(session, prop, listing)
    _record_verification(session, prop, listing, source=source, ps=ps, now=now)

    new_status = _resolve_status(session, prop, listing, source=source, before=before, now=now)
    prop.listing_status = new_status
    prop.fingerprint = fingerprint(prop, geo=geo)

    result = _decide_change(
        prop=prop,
        before=before,
        is_new=is_new,
        new_status=new_status,
        price_change=price_change,
        source_row_created=source_row_created,
        text_hash=listing.text_hash,
        verdict_reasons=verdict.reasons if is_new else [],
    )
    _write_history(session, prop, before=before, result=result, run_id=run_id, now=now)

    session.flush()
    return prop, result


# --------------------------------------------------------------------------- #
# Row creation
# --------------------------------------------------------------------------- #


def _create_bare_property(session: Session, listing: NormalizedListing, now: datetime) -> Property:
    """The minimum row that can carry a foreign key. Facts land afterwards."""
    prop = Property(
        public_id=f"hof-{uuid4().hex[:10]}",
        canonical_title=(listing.title or f"Objekt {listing.town or 'unbekannt'}")[:500],
        listing_status=ListingStatus.DISCOVERED,
        verification_status=VerificationStatus.UNVERIFIED,
        first_seen=now,
        last_seen=now,
        evidence={},
        building_features=[],
        outbuildings=[],
        special_features=[],
        exclusion_flags=[],
        llm_risks=[],
    )
    session.add(prop)
    session.flush()
    return prop


def _record_observation(
    session: Session,
    prop: Property,
    listing: NormalizedListing,
    *,
    source: Source,
    run_id: int | None,
    now: datetime,
) -> Observation:
    obs = Observation(
        property_id=prop.id,
        source_id=source.id,
        run_id=run_id,
        url=listing.url,
        scraped_at=listing.fetched_at or now,
        title=listing.title,
        description=listing.description,
        price=listing.price,
        price_type=listing.price_type,
        price_raw=listing.price_raw,
        land_sqm=listing.land_sqm,
        living_sqm=listing.living_sqm,
        usable_sqm=listing.usable_sqm,
        year_built=listing.year_built,
        town=listing.town,
        postcode=listing.postcode,
        listing_visible=bool(listing.listing_visible),
        http_status=listing.http_status,
        listing_text_hash=listing.text_hash,
        image_hashes=list(listing.image_hashes or []),
        source_date=listing.source_date,
        raw={
            "source_key": listing.source_key,
            "external_id": listing.external_id,
            "warnings": list(listing.warnings or []),
        },
    )
    session.add(obs)
    session.flush()
    return obs


def _last_text_hash(session: Session, property_id: int, source_id: int) -> str | None:
    return session.execute(
        select(Observation.listing_text_hash)
        .where(Observation.property_id == property_id, Observation.source_id == source_id)
        .order_by(Observation.scraped_at.desc(), Observation.id.desc())
        .limit(1)
    ).scalar_one_or_none()


# --------------------------------------------------------------------------- #
# Fact merging
# --------------------------------------------------------------------------- #


def _apply_facts(
    prop: Property,
    listing: NormalizedListing,
    *,
    source: Source,
    geo: GeoResult | None,
    now: datetime,
) -> None:
    overwrite = _rules.may_overwrite(source)

    title, _ = _rules.take_value(prop.canonical_title, listing.title, overwrite=overwrite)
    prop.canonical_title = (title or prop.canonical_title)[:500]

    for prop_field, listing_field in _FACT_FIELDS:
        value, _ = _rules.take_value(
            getattr(prop, prop_field), getattr(listing, listing_field), overwrite=overwrite
        )
        setattr(prop, prop_field, value)

    for prop_field, listing_field in _TAG_FIELDS:
        setattr(
            prop,
            prop_field,
            _rules.union_tags(getattr(prop, prop_field), getattr(listing, listing_field)),
        )

    for flag in _FLAG_FIELDS:
        setattr(prop, flag, bool(getattr(prop, flag)) or bool(getattr(listing, flag)))

    prop.evidence = _rules.merge_evidence(prop.evidence, listing.evidence)

    if geo is not None and geo.lat is not None and geo.lon is not None:
        if prop.lat is None or overwrite or _rules.geo_is_better(prop.geo_precision, geo.precision):
            prop.lat, prop.lon = geo.lat, geo.lon
            prop.geo_precision = geo.precision or "none"
        if geo.distance_air_km is not None:
            prop.distance_air_km = geo.distance_air_km
        if geo.routed:
            prop.distance_driving_km = geo.distance_driving_km
            prop.distance_driving_minutes = geo.distance_driving_minutes

    # Any sighting, by any source, updates last_seen. Only a verifying source
    # may claim the listing was *checked*.
    prop.last_seen = now
    if _rules.can_verify(source) and listing.listing_visible:
        prop.last_verified = now
        prop.verification_status = VerificationStatus.VERIFIED
        if listing.source_date is not None:
            prop.source_date = listing.source_date
    # A discovery source deliberately touches none of the three fields above.


def _apply_price(
    session: Session,
    prop: Property,
    listing: NormalizedListing,
    *,
    source: Source,
    before: _Before,
    now: datetime,
) -> ChangeResult | None:
    """Write the price under the source rules and journal any movement."""
    overwrite = _rules.may_overwrite(source)
    new_price, changed = _rules.take_value(prop.price, listing.price, overwrite=overwrite)
    price_type, _ = _rules.take_value(prop.price_type, listing.price_type, overwrite=overwrite)
    if price_type and price_type != "unknown":
        prop.price_type = price_type

    if not changed:
        return None

    old_price = before.price
    prop.price = new_price
    prop.price_date = now
    if prop.price_first is None:
        prop.price_first = new_price

    delta_abs: float | None = None
    delta_pct: float | None = None
    if old_price is not None and new_price is not None:
        delta_abs = new_price - old_price
        if abs(delta_abs) < PRICE_CHANGE_MIN_ABS:
            return None
        delta_pct = round(delta_abs / old_price * 100.0, 4) if old_price else None
        if delta_abs < 0:
            prop.price_reduction_count = (prop.price_reduction_count or 0) + 1

    session.add(
        PriceHistory(
            property_id=prop.id,
            observed_at=now,
            old_price=old_price,
            new_price=new_price,
            delta_abs=delta_abs,
            delta_pct=delta_pct,
            source_id=source.id,
        )
    )

    if not before.known:
        # A brand new property gets its baseline price point, not a "change".
        return None
    return ChangeResult(
        kind=ChangeKind.PRICE_CHANGE,
        old_price=old_price,
        new_price=new_price,
        delta_abs=delta_abs,
        delta_pct=delta_pct,
        detail=("price first known" if old_price is None else None),
    )


def _sync_property_source(
    session: Session,
    prop: Property,
    listing: NormalizedListing,
    *,
    source: Source,
    now: datetime,
) -> tuple[PropertySource, bool]:
    """One row per (source, url). Exactly one row carries ``is_best``."""
    ps = session.execute(
        select(PropertySource).where(
            PropertySource.source_id == source.id, PropertySource.url == listing.url
        )
    ).scalar_one_or_none()

    created = ps is None
    if ps is None:
        ps = PropertySource(
            property_id=prop.id,
            source_id=source.id,
            url=listing.url,
            role=source.role,
            is_primary_source=source.role == SourceRole.PRIMARY,
            first_seen=now,
        )
        session.add(ps)
    ps.property_id = prop.id
    ps.role = source.role
    ps.last_seen = now
    ps.last_listing_visible = bool(listing.listing_visible)
    if listing.external_id:
        ps.external_id = str(listing.external_id)
    overwrite = _rules.may_overwrite(source)
    for attr, value in (
        ("contact_name", listing.contact_name),
        ("contact_kind", listing.contact_kind),
        ("contact_detail", listing.contact_detail),
    ):
        merged, _ = _rules.take_value(getattr(ps, attr), value, overwrite=overwrite)
        setattr(ps, attr, merged)
    # A discovery source's crawl date is not a seller-side publication date.
    if _rules.can_verify(source) and listing.source_date is not None:
        ps.source_date = listing.source_date

    session.flush()
    session.refresh(prop, ["property_sources"])
    _rules.assign_best_source(prop)
    session.flush()
    return ps, created


def _sync_images(session: Session, prop: Property, listing: NormalizedListing) -> None:
    """Store image URLs and their perceptual hashes - dedupe's strongest signal."""
    if not listing.image_urls:
        return
    known = {img.url for img in prop.images}
    hashes = list(listing.image_hashes or [])
    for index, url in enumerate(listing.image_urls):
        if url in known:
            continue
        session.add(
            Image(
                property_id=prop.id,
                url=url,
                phash=hashes[index] if index < len(hashes) else None,
            )
        )
        known.add(url)
    session.flush()
    session.refresh(prop, ["images"])


def _record_verification(
    session: Session,
    prop: Property,
    listing: NormalizedListing,
    *,
    source: Source,
    ps: PropertySource,
    now: datetime,
) -> None:
    """Audit row for 'a source that is allowed to prove things actually looked'."""
    if not _rules.can_verify(source):
        return
    session.add(
        VerificationEvent(
            property_id=prop.id,
            source_id=source.id,
            checked_at=now,
            url=listing.url,
            outcome="verified" if listing.listing_visible else "gone",
            http_status=listing.http_status,
            detail=f"{source.key} role={source.role}",
        )
    )


# --------------------------------------------------------------------------- #
# Status and change decision
# --------------------------------------------------------------------------- #


def _resolve_status(
    session: Session,
    prop: Property,
    listing: NormalizedListing,
    *,
    source: Source,
    before: _Before,
    now: datetime,
) -> str:
    """What the listing status should be after this observation."""
    if _rules.can_verify(source) and not listing.listing_visible:
        if _still_visible_somewhere(session, prop, exclude_source_id=source.id):
            return before.status
        prop.removed_at = now
        return ListingStatus.REMOVED

    if _rules.can_verify(source):
        prop.removed_at = None
        return ListingStatus.ACTIVE

    # Discovery source: it may pull a dormant property back into the funnel,
    # but it can never promote it to ACTIVE - it cannot prove the listing lives.
    if before.status in _rules.DORMANT_STATUSES:
        prop.removed_at = None
        return ListingStatus.DISCOVERED
    return before.status


def _still_visible_somewhere(
    session: Session, prop: Property, *, exclude_source_id: int | None = None
) -> bool:
    """Does any *verifying* source still show this listing?

    Only verifiable evidence counts. A discovery source's cached copy is not
    proof that the listing lives, exactly as its silence is not proof that it
    died.
    """
    rows = session.execute(
        select(PropertySource, Source)
        .join(Source, Source.id == PropertySource.source_id)
        .where(PropertySource.property_id == prop.id)
    ).all()
    for ps, src in rows:
        if exclude_source_id is not None and ps.source_id == exclude_source_id:
            continue
        if src.can_verify and ps.last_listing_visible:
            return True
    return False


def _decide_change(
    *,
    prop: Property,
    before: _Before,
    is_new: bool,
    new_status: str,
    price_change: ChangeResult | None,
    source_row_created: bool,
    text_hash: str | None,
    verdict_reasons: list[str],
) -> ChangeResult:
    """Pick the single kind that describes this run's news about the property."""
    reactivated = before.known and before.status in _rules.DORMANT_STATUSES

    if is_new:
        detail = "; ".join(verdict_reasons[:6]) or None
        return ChangeResult(
            kind=ChangeKind.FIRST_SEEN,
            old_status=None,
            new_status=new_status,
            old_price=None,
            new_price=prop.price,
            detail=detail,
        )

    base = ChangeResult(
        kind=ChangeKind.UNCHANGED,
        old_status=before.status,
        new_status=new_status,
        old_price=before.price,
        new_price=prop.price,
    )

    if new_status == ListingStatus.REMOVED and before.status != ListingStatus.REMOVED:
        base.kind = ChangeKind.REMOVED
        base.detail = "a verifying source reported the listing gone"
        return base

    if price_change is not None:
        # A price move outranks everything, including the reactivation it may
        # share a run with - both are written to status_history.
        base.kind = ChangeKind.PRICE_CHANGE
        base.old_price = price_change.old_price
        base.new_price = price_change.new_price
        base.delta_abs = price_change.delta_abs
        base.delta_pct = price_change.delta_pct
        base.detail = (
            "reactivated and repriced" if reactivated else price_change.detail
        )
        return base

    if reactivated:
        base.kind = ChangeKind.REACTIVATED
        base.detail = f"seen again after {before.status}"
        return base

    if source_row_created:
        base.kind = ChangeKind.SOURCE_CHANGE
        base.detail = "a new source is advertising this property"
        return base

    if text_hash is not None and before.text_hash is not None and text_hash != before.text_hash:
        base.kind = ChangeKind.DESCRIPTION_CHANGE
        base.detail = "listing text changed"
        return base

    if new_status != before.status:
        base.kind = ChangeKind.STATUS_CHANGE
        return base

    return base


def _write_history(
    session: Session,
    prop: Property,
    *,
    before: _Before,
    result: ChangeResult,
    run_id: int | None,
    now: datetime,
) -> None:
    """Journal the run. ``status_history`` doubles as the weekly change log."""
    if result.kind == ChangeKind.FIRST_SEEN:
        if before.known:  # pragma: no cover - guarded by _decide_change
            raise AssertionError("FIRST_SEEN emitted for a property that already existed")
        session.add(
            StatusHistory(
                property_id=prop.id,
                observed_at=now,
                old_status=None,
                new_status=prop.listing_status,
                change_kind=ChangeKind.FIRST_SEEN,
                detail=result.detail,
                run_id=run_id,
            )
        )
        return

    reactivated = before.status in _rules.DORMANT_STATUSES and (
        prop.listing_status not in _rules.DORMANT_STATUSES
    )
    if reactivated:
        session.add(
            StatusHistory(
                property_id=prop.id,
                observed_at=now,
                old_status=before.status,
                new_status=prop.listing_status,
                change_kind=ChangeKind.REACTIVATED,
                detail=f"seen again after {before.status}",
                run_id=run_id,
            )
        )

    if result.kind in (ChangeKind.UNCHANGED,) or (
        result.kind == ChangeKind.REACTIVATED and reactivated
    ):
        return

    session.add(
        StatusHistory(
            property_id=prop.id,
            observed_at=now,
            old_status=before.status,
            new_status=prop.listing_status,
            change_kind=result.kind,
            detail=result.detail,
            run_id=run_id,
        )
    )
