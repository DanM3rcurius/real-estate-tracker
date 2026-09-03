"""Reading a property's memory.

Hofradar's whole point is that it remembers. Two questions get asked over and
over - "when did we *first* have any evidence of this place?" and "what changed
recently?" - and both must be answered from the append-only tables rather than
from ``Property.first_seen`` alone, because a row can be re-created, merged or
back-filled. Getting this wrong is what makes a tracker announce the same farm
as new every week, so the logic lives in exactly one place.

Timestamps are normalised to UTC-aware on the way out: SQLite hands back naive
datetimes even for ``DateTime(timezone=True)`` columns, and comparing those to
``datetime.now(UTC)`` raises.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

#: How far back "recently" reaches for the card chips, in days.
DEFAULT_WINDOW_DAYS = 7


def as_aware(value: datetime | None) -> datetime | None:
    """Naive datetimes from SQLite are treated as UTC."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def now_utc() -> datetime:
    return datetime.now(UTC)


def window_start(days: int = DEFAULT_WINDOW_DAYS, *, now: datetime | None = None) -> datetime:
    return (now or now_utc()) - timedelta(days=days)


def _collect(prop: Any, attribute: str, field_name: str) -> list[datetime]:
    rows = getattr(prop, attribute, None) or []
    out: list[datetime] = []
    for row in rows:
        moment = as_aware(getattr(row, field_name, None))
        if moment is not None:
            out.append(moment)
    return out


def earliest_evidence(prop: Any) -> datetime | None:
    """The oldest timestamp anywhere in this property's history.

    Includes observations, status history, price history and per-source first
    sightings - not just ``first_seen`` - because that column is the *summary*,
    and the summary is what a merge or a re-ingest can move forward.
    """
    candidates = [as_aware(getattr(prop, "first_seen", None))]
    candidates += _collect(prop, "observations", "scraped_at")
    candidates += _collect(prop, "status_history", "observed_at")
    candidates += _collect(prop, "price_history", "observed_at")
    candidates += _collect(prop, "property_sources", "first_seen")
    known = [c for c in candidates if c is not None]
    return min(known) if known else None


def is_genuinely_new(prop: Any, since: datetime) -> bool:
    """True only if the database has *no* evidence of this place before ``since``.

    This is the guard behind the absolute rule that a previously-known property
    is never reported as NEU. It fails closed: unknown history means "not new".
    """
    first = earliest_evidence(prop)
    if first is None:
        return False
    return first >= as_aware(since)


def price_events_since(prop: Any, since: datetime) -> list[Any]:
    """Price *changes* in the window - not the first price we ever learned.

    Ingest journals the opening price as a row with ``old_price = None``. That
    is the property becoming known, which the NEU chip already says; counting it
    here put a PREISAENDERUNG chip on every brand-new property, next to a NEU
    chip contradicting it.
    """
    cutoff = as_aware(since)
    return [
        row
        for row in (getattr(prop, "price_history", None) or [])
        if getattr(row, "old_price", None) is not None
        and (as_aware(getattr(row, "observed_at", None)) or now_utc()) >= cutoff
    ]


def status_events_since(prop: Any, since: datetime) -> list[Any]:
    cutoff = as_aware(since)
    return [
        row
        for row in (getattr(prop, "status_history", None) or [])
        if (as_aware(getattr(row, "observed_at", None)) or now_utc()) >= cutoff
    ]


def latest_price_change(prop: Any) -> Any | None:
    rows = sorted(
        getattr(prop, "price_history", None) or [],
        key=lambda r: as_aware(getattr(r, "observed_at", None)) or now_utc(),
    )
    return rows[-1] if rows else None


def total_price_delta_pct(prop: Any) -> float | None:
    """Change from the first ever asking price to today's, in percent."""
    first = getattr(prop, "price_first", None)
    current = getattr(prop, "price", None)
    if not first or not current:
        return None
    return (current - first) / first * 100.0


def timeline(prop: Any) -> list[dict[str, Any]]:
    """One merged, chronological list of everything that ever happened here.

    Feeds the dossier sentence "seit Februar bekannt, heute von 690k auf 595k
    gefallen" - so each entry carries a pre-built German phrase alongside the
    raw values, and the template only has to print it.
    """
    from hofradar.web.filters import de_date, de_eur, de_month_year

    events: list[dict[str, Any]] = []

    first = earliest_evidence(prop)
    if first is not None:
        events.append(
            {
                "at": first,
                "kind": "first_seen",
                "icon": "🆕",
                "title": "Erstmals erfasst",
                "text": f"Seit {de_month_year(first)} bekannt.",
            }
        )

    for row in getattr(prop, "price_history", None) or []:
        moment = as_aware(getattr(row, "observed_at", None))
        old, new = getattr(row, "old_price", None), getattr(row, "new_price", None)
        delta_pct = getattr(row, "delta_pct", None)
        if old is None:
            # The opening price: the property becoming known, not a movement.
            events.append(
                {
                    "at": moment,
                    "kind": "price_first",
                    "icon": "💶",
                    "title": "Erster bekannter Preis",
                    "text": f"Erster bekannter Preis: {de_eur(new)}.",
                }
            )
            continue
        direction = "gefallen" if (new or 0) < old else "gestiegen"
        text = f"Preis von {de_eur(old)} auf {de_eur(new)} {direction}."
        if delta_pct is not None:
            text += f" ({delta_pct:+.1f} %)"
        events.append(
            {
                "at": moment,
                "kind": "price_change",
                "icon": "🔻" if direction == "gefallen" else "🔺",
                "title": "Preisänderung",
                "text": text,
            }
        )

    for row in getattr(prop, "status_history", None) or []:
        moment = as_aware(getattr(row, "observed_at", None))
        old, new = getattr(row, "old_status", None), getattr(row, "new_status", None)
        detail = getattr(row, "detail", None)
        text = f"Status {old or 'unbekannt'} → {new}."
        if detail:
            text += f" {detail}"
        events.append(
            {
                "at": moment,
                "kind": getattr(row, "change_kind", "status_change"),
                "icon": "🔁",
                "title": "Statuswechsel",
                "text": text,
            }
        )

    for row in getattr(prop, "observations", None) or []:
        moment = as_aware(getattr(row, "scraped_at", None))
        visible = getattr(row, "listing_visible", True)
        events.append(
            {
                "at": moment,
                "kind": "observation",
                "icon": "👁",
                "title": "Beobachtung",
                "text": (
                    f"{de_date(moment)}: Quelle abgerufen"
                    + ("" if visible else " – Inserat nicht mehr erreichbar")
                ),
            }
        )

    events.sort(key=lambda e: e["at"] or now_utc())
    return events


def timeline_sentence(prop: Any, *, now: datetime | None = None) -> str:
    """The one-line summary the dossier opens with."""
    from hofradar.web.filters import de_eur, de_month_year, de_relative_days

    first = earliest_evidence(prop)
    parts: list[str] = []
    if first is not None:
        parts.append(f"Seit {de_month_year(first)} bekannt")
    change = latest_price_change(prop)
    if change is not None:
        when = de_relative_days(as_aware(getattr(change, "observed_at", None)), now=now or now_utc())
        old, new = getattr(change, "old_price", None), getattr(change, "new_price", None)
        direction = "gefallen" if (new or 0) < (old or 0) else "gestiegen"
        parts.append(f"{when} von {de_eur(old)} auf {de_eur(new)} {direction}")
    elif getattr(prop, "price", None) is not None:
        parts.append(f"unveränderter Preis {de_eur(prop.price)}")
    if not parts:
        return "Noch keine Historie erfasst."
    return ", ".join(parts) + "."
