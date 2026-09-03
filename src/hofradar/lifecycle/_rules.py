"""The small, sharp rules that every lifecycle decision is built from.

Kept in one place because they are policy, not mechanism: what a source is
allowed to prove, which status counts as alive, and how a "better" source row
is picked. Changing the project's mind about any of these should mean editing
exactly one function here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from hofradar.db.enums import ListingStatus, SourceRole
from hofradar.db.models import PropertySource, Source

#: Statuses that mean "we have not proven this listing is gone".
ALIVE_STATUSES = frozenset(
    {
        ListingStatus.DISCOVERED,
        ListingStatus.VERIFIED,
        ListingStatus.ACTIVE,
        ListingStatus.PRICE_CHANGED,
        ListingStatus.FORECLOSURE,
        ListingStatus.OFF_MARKET_SIGNAL,
    }
)

#: Statuses a re-sighting lifts back into the funnel (never as FIRST_SEEN).
#:
#: EXPIRED belongs here for the same reason REMOVED and STALE do: the listing
#: had stopped being carried and is being carried again, and invariant 2 says
#: that event is a REACTIVATED row in the append-only history, not a bare
#: status flip a later reader has to reconstruct. Leaving it out was the only
#: way a property could disappear from a source and come back with no
#: reappearance recorded under its own name.
#:
#: This deliberately does NOT put the fortnightly renewal back in the weekly
#: digest, which is what docs/DECISIONS.md entry 15 forbids: the *history*
#: records the reappearance, and ``hofradar.report.data`` decides separately
#: what is newsworthy - see ``_was_reactivated`` there, which skips a
#: reactivation out of EXPIRED. Nothing real is lost by that: an advert that
#: stays down long enough for its return to be news about the farmstead is
#: moved on to STALE by ``apply_stale_rules`` first (EXPIRED is in
#: STALE_ELIGIBLE_STATUSES below), so the digest still sees that reappearance
#: as a reactivation out of STALE.
DORMANT_STATUSES = frozenset(
    {ListingStatus.REMOVED, ListingStatus.STALE, ListingStatus.EXPIRED}
)

#: Statuses that ``apply_stale_rules`` may time out.
#:
#: EXPIRED belongs here and deliberately not in GONE_STATUSES: an expired
#: advert is not evidence the farmstead is gone, but it also cannot be
#: allowed to sit at full availability forever - a property that genuinely
#: sold has its advert expire in exactly the same way, and needs a path out
#: of the ranking too. If it renews, ingest re-sets it to ACTIVE long before
#: the stale clock (45 days) would fire, so a normal fortnightly cycle never
#: reaches this at all.
STALE_ELIGIBLE_STATUSES = frozenset(
    {ListingStatus.ACTIVE, ListingStatus.VERIFIED, ListingStatus.PRICE_CHANGED,
     ListingStatus.DISCOVERED, ListingStatus.EXPIRED}
)

_ROLE_RANK = {SourceRole.DISCOVERY: 0, SourceRole.LOCAL: 1, SourceRole.PRIMARY: 2}

_GEO_PRECISION_RANK = {"none": 0, "postcode": 1, "town": 2, "street": 3, "exact": 4}


def can_verify(source: Source) -> bool:
    """Only primary and local sources may prove that a listing is live or gone.

    An aggregator, a search-engine cache or a web archive can *find* a farm, but
    its silence proves nothing and its crawl date is not freshness evidence.
    """
    return bool(source.can_verify)


def may_overwrite(source: Source) -> bool:
    """A verifying source may replace a known fact; a discovery source may only
    fill a hole. This is what stops a stale aggregator copy from rewriting a
    price that a broker page already told us."""
    return can_verify(source)


def take_value(current: Any, incoming: Any, *, overwrite: bool) -> tuple[Any, bool]:
    """Conservative field write. Returns ``(value, changed)``.

    Never overwrites a known value with ``None``: absence of evidence in one
    crawl is not evidence of absence.
    """
    if incoming is None or (isinstance(incoming, str) and not incoming.strip()):
        return current, False
    if current is None or (isinstance(current, str) and not current.strip()):
        return incoming, True
    if overwrite and current != incoming:
        return incoming, True
    return current, False


def geo_is_better(current_precision: str | None, incoming_precision: str | None) -> bool:
    return _GEO_PRECISION_RANK.get(incoming_precision or "none", 0) > _GEO_PRECISION_RANK.get(
        current_precision or "none", 0
    )


def as_utc(value: datetime | None) -> datetime | None:
    """Attach UTC to a naive datetime.

    SQLite has no timezone type, so a value written as aware UTC comes back
    naive; comparing the two raises ``TypeError``. Every datetime comparison
    in this package goes through here first.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def source_rank(ps: PropertySource) -> tuple[int, float, datetime]:
    """Ordering for ``is_best``: primary role, then reliability, then recency."""
    reliability = ps.source.reliability if ps.source is not None else 0.0
    return (_ROLE_RANK.get(ps.role, 0), float(reliability or 0.0), as_utc(ps.last_seen))


def assign_best_source(prop: Any) -> None:
    """Set ``is_best`` on exactly one of the property's source rows."""
    rows = list(prop.property_sources)
    if not rows:
        return
    best = max(rows, key=source_rank)
    for row in rows:
        row.is_best = row is best


def union_tags(current: list[str] | None, incoming: list[str] | None) -> list[str]:
    """Tag lists accumulate. A crawl that did not mention a feature has not
    disproven it."""
    out = list(current or [])
    seen = set(out)
    for tag in incoming or []:
        if tag not in seen:
            out.append(tag)
            seen.add(tag)
    return out


def merge_evidence(current: dict | None, incoming: dict | None) -> dict:
    """Per field, the higher-confidence claim wins."""
    merged = dict(current or {})
    for field_name, entry in (incoming or {}).items():
        old = merged.get(field_name)
        if old is None or _confidence(entry) > _confidence(old):
            merged[field_name] = entry
    return merged


def _confidence(entry: Any) -> float:
    if isinstance(entry, dict):
        try:
            return float(entry.get("confidence") or 0.0)
        except (TypeError, ValueError):
            return 0.0
    return 0.0
