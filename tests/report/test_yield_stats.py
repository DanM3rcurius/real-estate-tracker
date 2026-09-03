"""How many in-radius properties did each source actually produce?

The number this answers is "was this source worth building", which no test of
parsing can answer. A source that parses perfectly and yields nothing inside
the radius is a source that should not have been built.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hofradar.report.yield_stats import YIELD_RADIUS_AIR_KM, source_yield

SINCE = datetime.now(UTC) - timedelta(days=28)


def _make_observation(session, *, property, source, scraped_at=None):
    """Append-only crawl record, built the way ingest would build one.

    ``Observation`` has no test factory of its own yet (only ``make_property``
    and ``make_source`` exist in the shared conftest), so this mirrors the
    fields ``hofradar.lifecycle.ingest`` sets - see
    ``tests/lifecycle/test_stale_and_changes.py`` for the production path.
    """
    from hofradar.db.models import Observation

    kwargs = {}
    if scraped_at is not None:
        kwargs["scraped_at"] = scraped_at
    observation = Observation(
        property_id=property.id,
        source_id=source.id,
        url=f"https://{source.key}.example/{property.public_id}",
        title=property.canonical_title,
        town=property.town,
        postcode=property.postcode,
        **kwargs,
    )
    session.add(observation)
    session.flush()
    return observation


def test_counts_in_radius_separately_from_observed(session, make_source, make_property) -> None:
    source = make_source(key="denkmalboerse")
    near = make_property(distance_air_km=12.0)
    far = make_property(distance_air_km=210.0)
    unknown = make_property(distance_air_km=None)
    for prop in (near, far, unknown):
        _make_observation(session, property=prop, source=source)
    session.flush()

    rows = source_yield(session, since=SINCE)

    assert len(rows) == 1
    assert rows[0].source_key == "denkmalboerse"
    assert rows[0].observed == 3
    # Unknown distance is not counted as in-radius: we did not prove it.
    assert rows[0].in_radius == 1


def test_a_source_with_no_observations_reports_zero(session, make_source) -> None:
    make_source(key="denkmalboerse")
    session.flush()

    rows = source_yield(session, since=SINCE)

    assert rows == [] or rows[0].observed == 0


def test_multiple_sources_are_reported_separately(session, make_source, make_property) -> None:
    a = make_source(key="source-a")
    b = make_source(key="source-b")
    prop_a = make_property(distance_air_km=10.0)
    prop_b = make_property(distance_air_km=200.0)
    _make_observation(session, property=prop_a, source=a)
    _make_observation(session, property=prop_b, source=b)
    session.flush()

    rows = {row.source_key: row for row in source_yield(session, since=SINCE)}

    assert set(rows) == {"source-a", "source-b"}
    assert rows["source-a"].observed == 1
    assert rows["source-a"].in_radius == 1
    assert rows["source-b"].observed == 1
    assert rows["source-b"].in_radius == 0


def test_a_property_observed_by_two_sources_counts_once_per_source(
    session, make_source, make_property
) -> None:
    """Breadth of offering (invariant-adjacent to PropertySource) must not double-count
    a source's own yield, and must not leak one source's find into another's row."""
    a = make_source(key="source-a")
    b = make_source(key="source-b")
    shared = make_property(distance_air_km=10.0)
    _make_observation(session, property=shared, source=a)
    _make_observation(session, property=shared, source=b)
    session.flush()

    rows = {row.source_key: row for row in source_yield(session, since=SINCE)}

    assert rows["source-a"].observed == 1
    assert rows["source-a"].in_radius == 1
    assert rows["source-b"].observed == 1
    assert rows["source-b"].in_radius == 1


def test_duplicate_observations_of_the_same_property_count_once(
    session, make_source, make_property
) -> None:
    """Two crawl runs of the same still-listed property must not double a source's yield."""
    source = make_source(key="denkmalboerse")
    prop = make_property(distance_air_km=5.0)
    _make_observation(session, property=prop, source=source, scraped_at=SINCE + timedelta(days=1))
    _make_observation(session, property=prop, source=source, scraped_at=SINCE + timedelta(days=8))
    session.flush()

    rows = source_yield(session, since=SINCE)

    assert rows[0].observed == 1
    assert rows[0].in_radius == 1


def test_since_excludes_observations_before_the_window(
    session, make_source, make_property
) -> None:
    source = make_source(key="denkmalboerse")
    old = make_property(distance_air_km=5.0)
    recent = make_property(distance_air_km=5.0)
    _make_observation(session, property=old, source=source, scraped_at=SINCE - timedelta(days=1))
    _make_observation(session, property=recent, source=source, scraped_at=SINCE + timedelta(days=1))
    session.flush()

    rows = source_yield(session, since=SINCE)

    assert rows[0].observed == 1
    assert rows[0].in_radius == 1


def test_the_radius_boundary_itself_counts_as_in_radius(
    session, make_source, make_property
) -> None:
    """<=, not <: a property exactly at the configured radius is inside it."""
    source = make_source(key="denkmalboerse")
    prop = make_property(distance_air_km=YIELD_RADIUS_AIR_KM)
    _make_observation(session, property=prop, source=source)
    session.flush()

    rows = source_yield(session, since=SINCE)

    assert rows[0].in_radius == 1


def test_radius_air_km_overrides_the_module_fallback(session, make_source, make_property) -> None:
    """build_report always passes the configured profile radius, not this module's
    fallback constant - a property outside the fallback but inside the configured
    radius must still be counted, e.g. 70 km under an 80 km profile."""
    source = make_source(key="denkmalboerse")
    prop = make_property(distance_air_km=70.0)
    _make_observation(session, property=prop, source=source)
    session.flush()

    narrow = source_yield(session, since=SINCE, radius_air_km=60.0)
    wide = source_yield(session, since=SINCE, radius_air_km=80.0)

    assert narrow[0].in_radius == 0
    assert wide[0].in_radius == 1
