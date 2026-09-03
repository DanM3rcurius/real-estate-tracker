"""Collapsing two rows that turned out to be one farm.

Merging is where memory is most easily lost, so the rules are conservative and
one-directional:

* history is *moved*, never dropped - every observation, price point, status
  transition, image and document ends up on the surviving row, so the audit
  trail of the merged-away listing survives the merge;
* facts are *filled in*, never blanked - a known value on ``keep`` is never
  replaced by ``NULL`` from ``drop``, and tag lists are unioned rather than
  overwritten;
* evidence is resolved per field by confidence, so the better-sourced claim
  wins instead of the last writer;
* ``first_seen`` takes the earliest and ``last_seen`` the latest of the two,
  because the merged property has genuinely been known since the earlier date;
* ``drop`` is never deleted. It keeps its id and gets ``merged_into_id`` set,
  so any external reference (a report, a bookmark, an old run) still resolves.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from hofradar.db.enums import ListingStatus, SourceRole, VerificationStatus
from hofradar.db.models import (
    Document,
    Image,
    Observation,
    PriceHistory,
    Property,
    PropertySource,
    StatusHistory,
)

#: Scalar facts copied from ``drop`` only where ``keep`` knows nothing.
_FILLABLE_FIELDS = (
    "canonical_title",
    "description",
    "street",
    "postcode",
    "town",
    "district",
    "state",
    "lat",
    "lon",
    "distance_air_km",
    "distance_driving_km",
    "distance_driving_minutes",
    "price",
    "price_date",
    "price_first",
    "land_sqm",
    "living_sqm",
    "usable_sqm",
    "rooms",
    "year_built",
    "condition",
    "property_type",
    "llm_summary",
    "user_state",
    "user_note",
)

#: JSON list columns that are unioned rather than replaced.
_LIST_FIELDS = (
    "building_features",
    "outbuildings",
    "special_features",
    "exclusion_flags",
    "llm_risks",
)

#: Boolean signal flags: a signal seen on either row is a signal on the merge.
_FLAG_FIELDS = (
    "is_foreclosure",
    "is_monument",
    "is_private_seller",
    "is_off_market_signal",
)

#: Higher is "more alive"; the surviving row takes the livelier status.
_STATUS_RANK = {
    ListingStatus.REMOVED: 0,
    ListingStatus.SOLD: 0,
    ListingStatus.STALE: 1,
    ListingStatus.DISCOVERED: 2,
    ListingStatus.OFF_MARKET_SIGNAL: 3,
    ListingStatus.FORECLOSURE: 3,
    ListingStatus.VERIFIED: 4,
    ListingStatus.PRICE_CHANGED: 5,
    ListingStatus.ACTIVE: 5,
}

_GEO_PRECISION_RANK = {"none": 0, "postcode": 1, "town": 2, "street": 3, "exact": 4}

_ROLE_RANK = {SourceRole.DISCOVERY: 0, SourceRole.LOCAL: 1, SourceRole.PRIMARY: 2}


def merge_properties(session: Session, keep: Property, drop: Property) -> Property:
    """Fold ``drop`` into ``keep`` and return ``keep``."""
    if keep.id == drop.id:
        return keep

    _move_property_sources(session, keep, drop)
    _move_images(session, keep, drop)
    for model in (Observation, PriceHistory, StatusHistory, Document):
        session.execute(
            update(model).where(model.property_id == drop.id).values(property_id=keep.id)
        )

    _merge_facts(keep, drop)
    _merge_evidence(keep, drop)

    drop.merged_into_id = keep.id
    session.flush()
    session.expire(keep)
    session.expire(drop)
    _assign_best_source(session, keep)
    session.flush()
    return keep


# --------------------------------------------------------------------------- #
# Relationship repointing
# --------------------------------------------------------------------------- #


def _move_property_sources(session: Session, keep: Property, drop: Property) -> None:
    """Repoint source rows, folding collisions on the (source, url) unique key."""
    existing = {(ps.source_id, ps.url): ps for ps in keep.property_sources}
    for ps in list(drop.property_sources):
        twin = existing.get((ps.source_id, ps.url))
        if twin is None:
            session.execute(
                update(PropertySource)
                .where(PropertySource.id == ps.id)
                .values(property_id=keep.id)
            )
            continue
        twin.first_seen = min(twin.first_seen, ps.first_seen)
        twin.last_seen = max(twin.last_seen, ps.last_seen)
        twin.last_listing_visible = twin.last_listing_visible or ps.last_listing_visible
        for attr in ("external_id", "source_date", "contact_name", "contact_kind",
                     "contact_detail"):
            if getattr(twin, attr, None) is None:
                setattr(twin, attr, getattr(ps, attr, None))
        session.delete(ps)
    session.flush()


def _move_images(session: Session, keep: Property, drop: Property) -> None:
    """Repoint images, dropping exact URL duplicates the survivor already has."""
    existing_urls = {img.url for img in keep.images}
    for img in list(drop.images):
        if img.url in existing_urls:
            session.delete(img)
            continue
        session.execute(
            update(Image).where(Image.id == img.id).values(property_id=keep.id)
        )
        existing_urls.add(img.url)
    session.flush()


# --------------------------------------------------------------------------- #
# Fact merging
# --------------------------------------------------------------------------- #


def _merge_facts(keep: Property, drop: Property) -> None:
    for name in _FILLABLE_FIELDS:
        if _is_blank(getattr(keep, name, None)) and not _is_blank(getattr(drop, name, None)):
            setattr(keep, name, getattr(drop, name))

    for name in _LIST_FIELDS:
        keep_list = list(getattr(keep, name) or [])
        seen = set(keep_list)
        for item in getattr(drop, name) or []:
            if item not in seen:
                keep_list.append(item)
                seen.add(item)
        setattr(keep, name, keep_list)

    for name in _FLAG_FIELDS:
        setattr(keep, name, bool(getattr(keep, name)) or bool(getattr(drop, name)))

    if _GEO_PRECISION_RANK.get(drop.geo_precision, 0) > _GEO_PRECISION_RANK.get(
        keep.geo_precision, 0
    ):
        keep.lat, keep.lon, keep.geo_precision = drop.lat, drop.lon, drop.geo_precision

    keep.first_seen = min(keep.first_seen, drop.first_seen)
    keep.last_seen = max(keep.last_seen, drop.last_seen)
    keep.last_verified = _max_optional(keep.last_verified, drop.last_verified)
    keep.source_date = _max_optional(keep.source_date, drop.source_date)
    keep.price_reduction_count = max(
        keep.price_reduction_count or 0, drop.price_reduction_count or 0
    )
    if keep.price_type in (None, "", "unknown") and drop.price_type:
        keep.price_type = drop.price_type

    if _STATUS_RANK.get(drop.listing_status, 2) > _STATUS_RANK.get(keep.listing_status, 2):
        keep.listing_status = drop.listing_status
    if keep.listing_status not in (ListingStatus.REMOVED, ListingStatus.SOLD):
        keep.removed_at = None
    if VerificationStatus.VERIFIED in (keep.verification_status, drop.verification_status):
        keep.verification_status = VerificationStatus.VERIFIED

    if keep.fingerprint is None:
        keep.fingerprint = drop.fingerprint


def _merge_evidence(keep: Property, drop: Property) -> None:
    """Union the evidence dicts, keeping the higher-confidence entry per field."""
    merged: dict[str, Any] = dict(keep.evidence or {})
    for field_name, entry in (drop.evidence or {}).items():
        current = merged.get(field_name)
        if current is None or _confidence(entry) > _confidence(current):
            merged[field_name] = entry
    keep.evidence = merged


def _assign_best_source(session: Session, prop: Property) -> None:
    """Exactly one ``is_best`` row: primary role, then reliability, then recency.

    Deliberately duplicated from ``hofradar.lifecycle`` rather than imported:
    the module contract only lets other packages import the four public dedupe
    names, and lifecycle already depends on dedupe.
    """
    rows = list(prop.property_sources)
    if not rows:
        return
    best = max(rows, key=_source_rank)
    for row in rows:
        row.is_best = row.id == best.id


def _source_rank(ps: PropertySource) -> tuple[int, float, Any]:
    reliability = ps.source.reliability if ps.source is not None else 0.0
    return (_ROLE_RANK.get(ps.role, 0), reliability, ps.last_seen)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _confidence(entry: Any) -> float:
    if isinstance(entry, dict):
        try:
            return float(entry.get("confidence") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0


def _max_optional(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return max(a, b)
