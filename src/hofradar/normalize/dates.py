"""German date parsing: absolute, spelled-out, ISO-week, and relative forms.

Sources report "when" in every shape a human editor might type: a dotted
numeric date, a spelled-out month, a newspaper's ISO week/year, or a relative
phrase from a portal's "last seen" badge ("vor 3 Tagen"). Every form
resolves to a single, unambiguous, timezone-aware UTC ``datetime`` so the
lifecycle stage never has to guess what "recently" means.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime

from dateutil.relativedelta import relativedelta

from hofradar.normalize.text import normalize_text

_MONTHS: dict[str, int] = {
    "januar": 1,
    "jaenner": 1,
    "februar": 2,
    "maerz": 3,
    "april": 4,
    "mai": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "dezember": 12,
}

_RELATIVE_RE = re.compile(r"vor\s+(\d+)\s+(tag|tage|woche|wochen|monat|monate)\b")
_KW_RE = re.compile(r"\bkw\s*(\d{1,2})\s*/\s*(\d{4})\b", re.IGNORECASE)
_MONTHNAME_RE = re.compile(r"\b(\d{1,2})\.\s*([A-Za-zÀ-ÿ]+)\s+(\d{4})\b")
_DOTTED_RE = re.compile(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b")


def _today_utc() -> datetime:
    return datetime.combine(datetime.now(timezone.utc).date(), dtime.min, tzinfo=timezone.utc)


def _as_utc_midnight(d: date) -> datetime:
    return datetime.combine(d, dtime.min, tzinfo=timezone.utc)


def parse_german_date(text: str | None) -> datetime | None:
    """Parse a German date expression into a tz-aware UTC ``datetime``.

    Recognises, in order:

    - relative phrases: "heute", "gestern", "vor 3 Tagen", "vor 2 Wochen",
      "vor 1 Monat" (relative to the moment this function is called);
    - ISO week: "KW 34/2026" (resolved to the Monday of that week);
    - spelled-out: "12. März 2026" (German month names, umlaut-insensitive);
    - dotted numeric: "12.03.2026" (day.month.year - the German order; a
      2-digit year is expanded to 19xx/20xx with a 1970 pivot);
    - ISO 8601: "2026-03-12", "2026-03-12T10:00:00Z", with or without an
      offset - a naive result is assumed to already be UTC.

    Returns ``None`` when nothing recognisable is found.
    """
    if not text or not text.strip():
        return None
    raw = text.strip()
    norm = normalize_text(raw)

    if norm == "heute":
        return _today_utc()
    if norm == "gestern":
        return _today_utc() - timedelta(days=1)

    rel_match = _RELATIVE_RE.search(norm)
    if rel_match:
        n, unit = int(rel_match.group(1)), rel_match.group(2)
        if unit.startswith("tag"):
            return _today_utc() - timedelta(days=n)
        if unit.startswith("woche"):
            return _today_utc() - timedelta(weeks=n)
        return _today_utc() - relativedelta(months=n)

    kw_match = _KW_RE.search(raw)
    if kw_match:
        week, year = int(kw_match.group(1)), int(kw_match.group(2))
        try:
            d = date.fromisocalendar(year, week, 1)
        except ValueError:
            return None
        return _as_utc_midnight(d)

    month_match = _MONTHNAME_RE.search(raw)
    if month_match:
        month = _MONTHS.get(normalize_text(month_match.group(2)))
        if month is not None:
            try:
                d = date(int(month_match.group(3)), month, int(month_match.group(1)))
            except ValueError:
                return None
            return _as_utc_midnight(d)

    dotted_match = _DOTTED_RE.search(raw)
    if dotted_match:
        day, month, year_str = (
            int(dotted_match.group(1)),
            int(dotted_match.group(2)),
            dotted_match.group(3),
        )
        year = int(year_str)
        if len(year_str) == 2:
            year += 2000 if year < 70 else 1900
        try:
            d = date(year, month, day)
        except ValueError:
            return None
        return _as_utc_midnight(d)

    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
