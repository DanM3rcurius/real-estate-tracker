"""Private helpers shared by the scoring components.

Nothing here is part of the public API of :mod:`hofradar.scoring` (see
``docs/MODULE_API.md``). The text folding is a deliberate twelve-line duplicate
of what ``hofradar.normalize`` does internally: the module contract says
packages import each other only through their published names, and all the
scores need is "does this listing mention this term".
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from hofradar.db.models import Property, PropertySource

#: Float slack for band comparisons. Bands are expressed as fractions of a
#: slider (e.g. 8/15 of the budget), so an exact boundary value such as
#: 400_000 / 750_000 must land *inside* the band it defines.
BAND_EPSILON = 1e-9

_UMLAUT_MAP = str.maketrans(
    {
        "ä": "ae",
        "ö": "oe",
        "ü": "ue",
        "ß": "ss",
        "-": " ",
        "/": " ",
        "_": " ",
        "\n": " ",
    }
)

#: Fields whose presence is counted by the completeness term of the confidence
#: score. Nine facts a serious listing states and a two-line classified does not.
COMPLETENESS_FIELDS: tuple[str, ...] = (
    "price",
    "land_sqm",
    "living_sqm",
    "year_built",
    "condition",
    "property_type",
    "town",
    "lat",
    "description",
)


def fold(text: str | None) -> str:
    """Casefold, expand umlauts, squash whitespace."""
    if not text:
        return ""
    return " ".join(str(text).casefold().translate(_UMLAUT_MAP).split())


def fold_all(values: Iterable[Any] | None) -> list[str]:
    if not values:
        return []
    return [folded for value in values if (folded := fold(str(value)))]


def contains_any(haystack: str, terms: Iterable[str]) -> str | None:
    """First term of ``terms`` present in ``haystack``, else ``None``."""
    for term in terms:
        if term in haystack:
            return term
    return None


def tag_blob(prop: Property) -> str:
    """Folded canonical tags only - no free prose, so no broker adjectives."""
    tags: list[str] = []
    for attr in ("building_features", "outbuildings", "special_features", "exclusion_flags"):
        tags.extend(fold_all(getattr(prop, attr, None)))
    return " | ".join(tags)


def text_blob(prop: Property) -> str:
    """Tags plus title, type and description. Used where prose is legitimate
    evidence (a Baurecht reference is quoted in the prose, not tagged)."""
    parts = [
        tag_blob(prop),
        fold(getattr(prop, "canonical_title", None)),
        fold(getattr(prop, "property_type", None)),
        fold(getattr(prop, "description", None)),
    ]
    return " | ".join(part for part in parts if part)


def band(value: float, bands: Sequence[tuple[float, float]], default: float = 0.0) -> float:
    """First ``points`` whose inclusive ``threshold`` the value does not exceed."""
    for threshold, points in bands:
        if value <= threshold + BAND_EPSILON:
            return points
    return default


def band_below(value: float, bands: Sequence[tuple[float, float]], default: float) -> float:
    """Like :func:`band` but with *exclusive* thresholds (``value < threshold``)."""
    for threshold, points in bands:
        if value < threshold - BAND_EPSILON:
            return points
    return default


def to_utc(value: datetime | None) -> datetime | None:
    """Normalise a timestamp to aware UTC.

    SQLite hands back naive datetimes even for ``DateTime(timezone=True)``
    columns, and every score subtracts a stored timestamp from ``now``.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def now_utc(now: datetime | None = None) -> datetime:
    return to_utc(now) or datetime.now(UTC)


def days_since(value: datetime | None, now: datetime) -> float | None:
    """Whole days between ``value`` and ``now``; ``None`` if unknown.

    A timestamp in the future counts as zero days old rather than negative.
    """
    stamped = to_utc(value)
    if stamped is None:
        return None
    return max(0.0, (now - stamped).total_seconds() / 86_400.0)


def property_sources(prop: Property) -> list[PropertySource]:
    return list(getattr(prop, "property_sources", None) or [])


def verifying_sources(prop: Property) -> list[PropertySource]:
    """Only PRIMARY and LOCAL sources may prove that something is true.

    A discovery source (a search engine, an aggregator, a cache) proves only
    that a page once existed somewhere - never that a listing is live or fresh.
    """
    return [ps for ps in property_sources(prop) if ps.source is not None and ps.source.can_verify]


def best_source(prop: Property) -> PropertySource | None:
    """The most trustworthy source row: verifying first, then by reliability."""
    rows = [ps for ps in property_sources(prop) if ps.source is not None]
    if not rows:
        return None
    return max(
        rows,
        key=lambda ps: (
            bool(ps.is_best),
            bool(ps.source.can_verify),
            float(ps.source.reliability or 0.0),
        ),
    )
