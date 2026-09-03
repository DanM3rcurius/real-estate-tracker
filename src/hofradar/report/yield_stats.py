"""Per-source yield: the number that says whether a source was worth building.

A source can parse flawlessly and still be useless, because parsing is not
yield. This module answers the only question that matters about a new
adapter - how many properties inside the radius did it actually produce - and
puts it in the weekly report where it cannot be avoided.

An unknown air distance is deliberately not counted as in-radius. We did not
prove the property is near; treating unknown as near would be the same mistake
as letting air distance stand in for road distance (invariant 3).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import case, func, select

from hofradar.db.models import Observation, Property, Source

if TYPE_CHECKING:  # pragma: no cover
    from datetime import datetime

    from sqlalchemy.orm import Session

#: Fallback air-kilometre threshold for a caller with no profile to hand -
#: e.g. an ad-hoc query. ``build_report`` always passes the *configured*
#: ``profile.radius.air_km_max`` instead, because the go/no-go this feeds
#: ("fewer than 5 in-radius objects across four runs") must be judged against
#: the radius the user actually set, not a number transcribed once and then
#: forgotten. Air rather than road because the question is "did this source
#: find things near us", not "how far is the drive" - and most rows are never
#: routed.
YIELD_RADIUS_AIR_KM = 60.0


@dataclass(slots=True)
class SourceYield:
    source_key: str
    observed: int
    in_radius: int


def source_yield(
    session: Session, *, since: datetime, radius_air_km: float | None = None
) -> list[SourceYield]:
    """Distinct properties each source observed since ``since``, and how many were near.

    ``radius_air_km`` defaults to :data:`YIELD_RADIUS_AIR_KM` only when the
    caller has no profile to pass; ``build_report`` always supplies
    ``profile.radius.air_km_max`` so "in radius" here means the same thing as
    "in radius" everywhere else in the report.

    Counted with ``count(distinct(case(...)))`` rather than an arithmetic trick
    on ``Property.id`` (multiplying an id by a boolean condition) - that
    construction is dialect-dependent and its behaviour on SQLite, which the
    whole suite runs against, is not something to trust without a real
    database in front of you. A ``CASE`` that yields either the id or ``NULL``
    is standard SQL and ``count(DISTINCT ...)`` already ignores ``NULL``.
    """
    threshold = YIELD_RADIUS_AIR_KM if radius_air_km is None else radius_air_km
    in_radius_id = case(
        (Property.distance_air_km <= threshold, Property.id),
        else_=None,
    )
    rows = session.execute(
        select(
            Source.key,
            func.count(func.distinct(Property.id)),
            func.count(func.distinct(in_radius_id)),
        )
        .join(Observation, Observation.source_id == Source.id)
        .join(Property, Property.id == Observation.property_id)
        .where(Observation.scraped_at >= since)
        .group_by(Source.key)
        .order_by(Source.key)
    ).all()
    return [SourceYield(source_key=key, observed=obs, in_radius=near) for key, obs, near in rows]


@dataclass(slots=True)
class MunicipalityCoverage:
    town: str
    observed: int


def coverage_by_municipality(
    session: Session, *, since: datetime, expected: list[str]
) -> list[MunicipalityCoverage]:
    """Observations per expected municipality, including the ones with none.

    The zeros are the entire point, which is why ``expected`` is a required
    argument rather than something derived from the data: a municipality that
    produced nothing cannot appear in a query over what was produced. Naming
    the towns we believe are in range is what makes their silence legible.
    """
    counts = dict(
        session.execute(
            select(Property.town, func.count(func.distinct(Property.id)))
            .join(Observation, Observation.property_id == Property.id)
            .where(Observation.scraped_at >= since, Property.town.in_(expected))
            .group_by(Property.town)
        ).all()
    )
    return [MunicipalityCoverage(town=town, observed=counts.get(town, 0)) for town in expected]
