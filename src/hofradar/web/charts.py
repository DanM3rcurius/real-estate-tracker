"""Tiny inline SVG helpers.

A price history is three numbers on a good day, so a charting library would be
more bytes than the whole page. These functions return plain geometry that the
templates turn into ``<svg>`` - no runtime dependency, works with JavaScript off.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from hofradar.web import history


@dataclass(slots=True)
class Sparkline:
    points: str
    dots: list[tuple[float, float]]
    width: float
    height: float
    low: float
    high: float
    first: float
    last: float
    trend: str  # "down" | "up" | "flat"
    labels: list[tuple[float, float, str]]

    @property
    def has_data(self) -> bool:
        return bool(self.dots)


def price_series(prop: Any) -> list[tuple[Any, float]]:
    """Chronological ``(when, price)`` pairs, oldest first, current price last."""
    series: list[tuple[Any, float]] = []
    rows = sorted(
        getattr(prop, "price_history", None) or [],
        key=lambda r: history.as_aware(getattr(r, "observed_at", None)) or history.now_utc(),
    )
    for row in rows:
        when = history.as_aware(getattr(row, "observed_at", None))
        if getattr(row, "old_price", None) is not None and not series:
            series.append((history.as_aware(getattr(prop, "first_seen", None)), float(row.old_price)))
        if getattr(row, "new_price", None) is not None:
            series.append((when, float(row.new_price)))
    current = getattr(prop, "price", None)
    if current is not None:
        if not series or abs(series[-1][1] - float(current)) > 0.5:
            series.append((history.as_aware(getattr(prop, "last_seen", None)), float(current)))
    return series


def sparkline(prop: Any, *, width: float = 240.0, height: float = 48.0, pad: float = 6.0) -> Sparkline:
    series = price_series(prop)
    if len(series) < 2:
        values = [v for _, v in series]
        return Sparkline(
            points="",
            dots=[],
            width=width,
            height=height,
            low=min(values) if values else 0.0,
            high=max(values) if values else 0.0,
            first=values[0] if values else 0.0,
            last=values[-1] if values else 0.0,
            trend="flat",
            labels=[],
        )

    values = [v for _, v in series]
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    step = (width - 2 * pad) / (len(values) - 1)

    dots: list[tuple[float, float]] = []
    for index, value in enumerate(values):
        x = pad + index * step
        y = height - pad - ((value - low) / span) * (height - 2 * pad)
        dots.append((round(x, 2), round(y, 2)))

    first, last = values[0], values[-1]
    trend = "flat"
    if last < first:
        trend = "down"
    elif last > first:
        trend = "up"

    labels = [(dots[0][0], dots[0][1], f"{first:,.0f}"), (dots[-1][0], dots[-1][1], f"{last:,.0f}")]

    return Sparkline(
        points=" ".join(f"{x},{y}" for x, y in dots),
        dots=dots,
        width=width,
        height=height,
        low=low,
        high=high,
        first=first,
        last=last,
        trend=trend,
        labels=labels,
    )
