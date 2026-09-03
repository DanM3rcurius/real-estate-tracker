"""Read-through cache for geocoding and routing results.

Both upstream providers are free, rate-limited and slow; re-asking the same
question on every pipeline run would be both rude and pointless. Negative
results (an address Nominatim cannot resolve, a route OSRM cannot compute)
are cached too, so a permanently-bad address is not re-queried on every
single run - the TTL is what eventually lets it be retried in case the
provider's data improves.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hofradar.db.models import GeoCache

#: How long a cached geocode result is trusted before we ask again.
GEOCODE_TTL = timedelta(days=90)
#: Routes change less often than addresses get re-typed; cache them longer.
ROUTE_TTL = timedelta(days=180)

_TTL_BY_KIND: dict[str, timedelta] = {"geocode": GEOCODE_TTL, "route": ROUTE_TTL}


def _now() -> datetime:
    return datetime.now(UTC)


def cache_get(session: Session, kind: str, key: str) -> dict[str, Any] | None:
    """Return the cached payload for ``(kind, key)``, or ``None`` on a miss or expiry."""
    row = session.execute(
        select(GeoCache).where(GeoCache.kind == kind, GeoCache.key == key)
    ).scalar_one_or_none()
    if row is None:
        return None

    created_at = row.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    ttl = _TTL_BY_KIND.get(kind, GEOCODE_TTL)
    if _now() - created_at > ttl:
        return None
    return row.payload


def cache_put(session: Session, kind: str, key: str, payload: dict[str, Any]) -> None:
    """Insert or refresh the cached payload for ``(kind, key)``.

    Flushes so the write is visible to later queries in the same session, but
    deliberately does not commit - the caller (the pipeline, or a test) owns
    the transaction boundary.
    """
    row = session.execute(
        select(GeoCache).where(GeoCache.kind == kind, GeoCache.key == key)
    ).scalar_one_or_none()
    if row is None:
        session.add(GeoCache(kind=kind, key=key, payload=payload, created_at=_now()))
    else:
        row.payload = payload
        row.created_at = _now()
    session.flush()
