"""German output formatting.

The user reads this UI in German, so every number that reaches a template goes
through here: ``1.234.567 €``, ``42,3 km``, ``03.09.2026``. Formatting lives in
one module rather than in the templates because the weekly report renders the
same values without Jinja, and both must agree to the last decimal.

Code, identifiers and docstrings stay English; only the produced copy is German.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

#: What we print when a fact is simply not known. Never print ``0`` or ``-``
#: for a missing value - an unknown price is not a free house.
UNKNOWN = "k. A."

#: Distinct from :data:`UNKNOWN`: the fact exists, we just have not measured it.
NOT_CHECKED = "nicht geprüft"

MONTHS_DE = (
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)

WEEKDAYS_DE = ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag")

_SEP_PLACEHOLDER = "\x00"


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def de_number(value: Any, decimals: int = 0) -> str:
    """``1234567.5`` -> ``1.234.567,5`` (de-DE grouping)."""
    number = _as_float(value)
    if number is None:
        return UNKNOWN
    formatted = f"{number:,.{decimals}f}"
    return (
        formatted.replace(",", _SEP_PLACEHOLDER).replace(".", ",").replace(_SEP_PLACEHOLDER, ".")
    )


def de_eur(value: Any, decimals: int = 0) -> str:
    """``690000`` -> ``690.000 €``."""
    number = _as_float(value)
    if number is None:
        return UNKNOWN
    return f"{de_number(number, decimals)} €"


def de_eur_short(value: Any) -> str:
    """Compact money for badges and chips: ``690.000 €`` -> ``690 Tsd.``."""
    number = _as_float(value)
    if number is None:
        return UNKNOWN
    if abs(number) >= 1_000_000:
        return f"{de_number(number / 1_000_000, 2)} Mio."
    if abs(number) >= 1_000:
        return f"{de_number(number / 1_000, 0)} Tsd."
    return de_number(number, 0)


def de_km(value: Any, decimals: int = 1) -> str:
    """``42.34`` -> ``42,3 km``. ``None`` is *unknown*, never zero."""
    number = _as_float(value)
    if number is None:
        return UNKNOWN
    return f"{de_number(number, decimals)} km"


def de_driving_km(value: Any, decimals: int = 1) -> str:
    """Driving distance only.

    A missing road route renders as ``nicht geprüft`` and NEVER falls back to
    the air distance - conflating the two is the single most misleading thing
    this UI could do.
    """
    number = _as_float(value)
    if number is None:
        return NOT_CHECKED
    return de_km(number, decimals)


def de_minutes(value: Any) -> str:
    number = _as_float(value)
    if number is None:
        return NOT_CHECKED
    return f"{de_number(number, 0)} min"


def de_sqm(value: Any, decimals: int = 0) -> str:
    number = _as_float(value)
    if number is None:
        return UNKNOWN
    return f"{de_number(number, decimals)} m²"


def de_pct(value: Any, decimals: int = 1) -> str:
    """Takes a *percentage* number (``-13.8``), not a fraction."""
    number = _as_float(value)
    if number is None:
        return UNKNOWN
    sign = "+" if number > 0 else ""
    return f"{sign}{de_number(number, decimals)} %"


def _coerce_datetime(value: Any) -> datetime | date | None:
    if isinstance(value, datetime | date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def de_date(value: Any) -> str:
    moment = _coerce_datetime(value)
    if moment is None:
        return UNKNOWN
    return moment.strftime("%d.%m.%Y")


def de_datetime(value: Any) -> str:
    moment = _coerce_datetime(value)
    if moment is None:
        return UNKNOWN
    if isinstance(moment, datetime):
        return moment.strftime("%d.%m.%Y %H:%M")
    return de_date(moment)


def de_month(value: Any) -> str:
    """``2026-02-11`` -> ``Februar``. Powers the dossier timeline sentence."""
    moment = _coerce_datetime(value)
    if moment is None:
        return UNKNOWN
    return MONTHS_DE[moment.month - 1]


def de_month_year(value: Any) -> str:
    moment = _coerce_datetime(value)
    if moment is None:
        return UNKNOWN
    return f"{MONTHS_DE[moment.month - 1]} {moment.year}"


def de_relative_days(value: Any, *, now: datetime | None = None) -> str:
    """``vor 3 Tagen`` / ``heute`` - the freshness cue on a result card."""
    moment = _coerce_datetime(value)
    if moment is None:
        return UNKNOWN
    if isinstance(moment, date) and not isinstance(moment, datetime):
        moment = datetime(moment.year, moment.month, moment.day)
    reference = now or datetime.now(tz=moment.tzinfo)
    if reference.tzinfo is None and moment.tzinfo is not None:
        moment = moment.replace(tzinfo=None)
    if reference.tzinfo is not None and moment.tzinfo is None:
        moment = moment.replace(tzinfo=reference.tzinfo)
    days = (reference - moment).days
    if days <= 0:
        return "heute"
    if days == 1:
        return "gestern"
    if days < 31:
        return f"vor {days} Tagen"
    months = days // 30
    if months == 1:
        return "vor einem Monat"
    if months < 12:
        return f"vor {months} Monaten"
    years = days // 365
    return "vor einem Jahr" if years == 1 else f"vor {years} Jahren"


def de_score(value: Any) -> str:
    """Scores are 0-100 and always shown without decimals; 0 is a real value."""
    number = _as_float(value)
    if number is None:
        return UNKNOWN
    return de_number(round(number), 0)


def week_label(value: Any) -> str:
    """``KW 35 / 2026`` - the report header the user recognises."""
    moment = _coerce_datetime(value) or datetime.now()
    iso = moment.isocalendar()
    return f"KW {iso.week:02d} / {iso.year}"


#: German names for ``ListingStatus`` values - the reader never sees the enum.
STATUS_LABELS: dict[str, str] = {
    "discovered": "Entdeckt",
    "verified": "Verifiziert",
    "active": "Aktiv",
    "price_changed": "Preis geändert",
    "stale": "Veraltet",
    "foreclosure": "Zwangsversteigerung",
    "off_market_signal": "Off-Market-Signal",
    "removed": "Entfernt",
    "expired": "Anzeige abgelaufen",
    "sold": "Verkauft",
}


def de_status(value: Any) -> str:
    """The German name of a listing status; an unknown value passes through."""
    if value is None:
        return "unbekannt"
    return STATUS_LABELS.get(str(value), str(value))


#: Registered on the Jinja environment by :func:`hofradar.web.app.create_app`.
JINJA_FILTERS = {
    "de_number": de_number,
    "de_eur": de_eur,
    "de_eur_short": de_eur_short,
    "de_km": de_km,
    "de_driving_km": de_driving_km,
    "de_minutes": de_minutes,
    "de_sqm": de_sqm,
    "de_pct": de_pct,
    "de_date": de_date,
    "de_datetime": de_datetime,
    "de_month": de_month,
    "de_month_year": de_month_year,
    "de_relative_days": de_relative_days,
    "de_score": de_score,
    "week_label": week_label,
    "de_status": de_status,
}
