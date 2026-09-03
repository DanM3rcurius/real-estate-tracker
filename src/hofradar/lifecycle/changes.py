"""Reading the memory back out.

``status_history`` is written by every lifecycle decision, including the ones
that do not actually change the status (a price move, a new source, a rewritten
description). That makes it the single table the weekly report has to read, and
it means the report can never disagree with what ingest decided.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hofradar.db.enums import ChangeKind
from hofradar.db.models import PriceHistory, Property, StatusHistory


def changes_since(
    session: Session,
    since: datetime,
    *,
    kinds: list[str] | None = None,
) -> list[dict]:
    """Every reportable change since ``since``, newest first.

    ``kinds`` filters on :class:`~hofradar.db.enums.ChangeKind` values. The
    dicts are shaped for the weekly report and are safe to serialise.
    """
    stmt = (
        select(StatusHistory, Property)
        .join(Property, Property.id == StatusHistory.property_id)
        .where(StatusHistory.observed_at >= since, Property.merged_into_id.is_(None))
        .order_by(StatusHistory.observed_at.desc(), StatusHistory.id.desc())
    )
    if kinds:
        stmt = stmt.where(StatusHistory.change_kind.in_([str(k) for k in kinds]))

    out: list[dict[str, Any]] = []
    for history, prop in session.execute(stmt).all():
        entry: dict[str, Any] = {
            "property_id": prop.id,
            "public_id": prop.public_id,
            "kind": history.change_kind,
            "title": prop.canonical_title,
            "town": prop.town,
            "postcode": prop.postcode,
            "price": prop.price,
            "price_first": prop.price_first,
            "land_sqm": prop.land_sqm,
            "living_sqm": prop.living_sqm,
            "listing_status": prop.listing_status,
            "verification_status": prop.verification_status,
            "old_status": history.old_status,
            "new_status": history.new_status,
            "observed_at": history.observed_at,
            "detail": history.detail,
            "run_id": history.run_id,
            "url": _best_url(prop),
            "source_count": prop.source_count,
        }
        if history.change_kind == ChangeKind.PRICE_CHANGE:
            entry.update(_price_move(session, prop.id, history.observed_at))
        out.append(entry)
    return out


def _best_url(prop: Property) -> str | None:
    for ps in prop.property_sources:
        if ps.is_best:
            return ps.url
    return prop.property_sources[0].url if prop.property_sources else None


def _price_move(session: Session, property_id: int, at: datetime) -> dict[str, Any]:
    """The price point that belongs to this status-history row."""
    row = session.execute(
        select(PriceHistory)
        .where(PriceHistory.property_id == property_id, PriceHistory.observed_at <= at)
        .order_by(PriceHistory.observed_at.desc(), PriceHistory.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return {}
    return {
        "old_price": row.old_price,
        "new_price": row.new_price,
        "delta_abs": row.delta_abs,
        "delta_pct": row.delta_pct,
    }
