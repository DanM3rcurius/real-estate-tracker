"""Load the synthetic demo dataset into a database.

This exists so the public GitHub Pages snapshot has something to render without
publishing the owner's real search history, and so CI can exercise the whole
read path against a database that is not empty.

Two things about *how* it loads matter more than what it loads.

It goes through :func:`hofradar.lifecycle.ingest` like any crawled listing,
because ingest is the only writer of ``Property`` rows and it writes the
``Observation`` first. A demo path that inserted rows directly would be a second
writer, and the invariant would then hold only by everyone's good intentions.

And it hands ingest a ``NormalizedListing`` produced by the real normalizer from
authored expose text, rather than a hand-filled dataclass. So the property type,
feature tags, price type and the foreclosure/monument/private-seller booleans in
the published snapshot are derived by the code under test. A fixture that
declared them would make the demo prove nothing about the pipeline.

The dataset carries coordinates rather than addresses to geocode: seeding must
work with no network at all. Where an entry has no ``driving_km`` the road
distance stays ``None`` and ``routed`` stays False - an unknown road distance is
a different fact from the air distance, never a fallback for it.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from hofradar.config import SearchProfile
from hofradar.contracts import GeoResult, RawListing
from hofradar.db.models import Source

log = logging.getLogger(__name__)

#: The source every synthetic row is attributed to. Declared in
#: config/sources.yaml as disabled, primary role, and it enumerates nothing.
DEMO_SOURCE_KEY = "demo_seed"

#: Relative to the repository root; overridable so tests can point elsewhere.
DEFAULT_SEED_PATH = Path("data/seed/demo_listings.yaml")


class DemoSeedError(RuntimeError):
    """The seed file is missing or does not say what it must say."""


def seed_path(explicit: Path | None = None) -> Path:
    """Resolve the dataset, searching upwards like the config loader does."""
    if explicit is not None:
        return explicit
    here = Path.cwd()
    for candidate in (here, *here.parents):
        found = candidate / DEFAULT_SEED_PATH
        if found.exists():
            return found
    return DEFAULT_SEED_PATH


def load_seed_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise DemoSeedError(f"demo dataset not found at {path}")
    with path.open(encoding="utf-8") as fh:
        payload = yaml.safe_load(fh) or {}
    if not payload.get("listings"):
        raise DemoSeedError(f"{path} contains no listings")
    # The banner on the published site is driven by this flag, and the whole
    # premise of publishing the snapshot is that the data is invented. Refuse
    # to seed a file that does not say so, rather than quietly publishing
    # something real under a "demo" label.
    if not payload.get("meta", {}).get("fictional"):
        raise DemoSeedError(f"{path} does not declare meta.fictional: true")
    return payload


def _demo_source(session: Session) -> Source:
    source = session.scalars(select(Source).where(Source.key == DEMO_SOURCE_KEY)).one_or_none()
    if source is None:
        raise DemoSeedError(
            f"source {DEMO_SOURCE_KEY!r} is not registered - run 'hofradar init-db' first"
        )
    return source


def _raw_listing(entry: dict[str, Any]) -> RawListing:
    """One YAML entry -> the same shape a source adapter would have produced."""
    return RawListing(
        source_key=DEMO_SOURCE_KEY,
        url=entry["url"],
        title=entry.get("title"),
        description=entry.get("description"),
        price_raw=entry.get("price_raw"),
        land_raw=entry.get("land_raw"),
        living_raw=entry.get("living_raw"),
        rooms_raw=entry.get("rooms_raw"),
        year_raw=entry.get("year_raw"),
        location_raw=entry.get("location_raw"),
        postcode=entry.get("postcode"),
        town=entry.get("town"),
        external_id=entry.get("external_id"),
        source_date_raw=entry.get("source_date"),
        fetched_at=datetime.now(UTC),
    )


def _geo_result(entry: dict[str, Any], profile: SearchProfile) -> GeoResult | None:
    """Build the geo facts from the file, computing only the air distance."""
    from hofradar.geo import haversine_km

    geo = entry.get("geo") or {}
    lat, lon = geo.get("lat"), geo.get("lon")
    if lat is None or lon is None:
        return None

    centre = (profile.center.lat, profile.center.lon)
    driving_km = geo.get("driving_km")
    return GeoResult(
        lat=lat,
        lon=lon,
        precision=geo.get("precision", "none"),
        distance_air_km=round(haversine_km(centre, (lat, lon)), 2),
        # Absent from the file means no route was ever computed. It stays
        # unknown; it never inherits the air distance above.
        distance_driving_km=driving_km,
        distance_driving_minutes=geo.get("driving_minutes"),
        display_name=entry.get("location_raw"),
        provider="demo_seed",
        routed=driving_km is not None,
    )


def seed_demo(
    session: Session,
    profile: SearchProfile,
    *,
    path: Path | None = None,
    rescore: bool = True,
) -> int:
    """Ingest the synthetic dataset. Idempotent - re-running updates, never duplicates.

    Returns the number of listings ingested. Dedupe collapses a second run onto
    the same properties, so the property count stays put while the observation
    count grows, which is exactly what a re-crawl of unchanged listings does.
    """
    from hofradar.config import load_keywords
    from hofradar.lifecycle import ingest
    from hofradar.normalize import normalize_listing

    payload = load_seed_file(seed_path(path))
    source = _demo_source(session)
    keywords = load_keywords()

    count = 0
    for entry in payload["listings"]:
        listing = normalize_listing(_raw_listing(entry), keywords)
        ingest(session, listing, source=source, geo=_geo_result(entry, profile))
        count += 1

    session.flush()
    log.info("demo: ingested %d synthetic listings", count)

    if rescore:
        from hofradar.scoring import rescore_all

        scored = rescore_all(session, profile, only_dirty=False)
        log.info("demo: scored %d properties", scored)

    return count
