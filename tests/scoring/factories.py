"""Object builders for the scoring tests.

Kept out of ``conftest.py`` so the fixtures stay readable and so the builders
can be imported explicitly by name.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import count
from typing import Any

from sqlalchemy.orm import Session

from hofradar.db.enums import ListingStatus, PriceType, SourceRole, VerificationStatus
from hofradar.db.models import Property, PropertySource, Source

_PUBLIC_IDS = count(1)


def make_source(
    session: Session,
    *,
    key: str = "portal",
    role: str = SourceRole.PRIMARY,
    reliability: float = 0.9,
    name: str | None = None,
) -> Source:
    source = Source(key=key, name=name or key, role=role, reliability=reliability)
    session.add(source)
    session.flush()
    return source


def make_property(session: Session | None = None, **kwargs: Any) -> Property:
    """A plausible farmstead that sits comfortably inside the default budget,
    so that a test can isolate one signal without tripping a money gate."""
    defaults: dict[str, Any] = {
        "public_id": f"HR{next(_PUBLIC_IDS):05d}",
        "canonical_title": "Hofstelle mit Scheune und Stall",
        "description": "Sacherl in Alleinlage mit Scheune, Stall und Tenne. " * 12,
        "town": "Bad Feilnbach",
        "postcode": "83075",
        "geo_precision": "exact",
        "distance_air_km": 25.0,
        "distance_driving_km": 32.0,
        "price": 380_000.0,
        "price_type": PriceType.ASKING,
        "land_sqm": 6_000.0,
        "living_sqm": 150.0,
        "year_built": 1890,
        "condition": None,
        "property_type": "Hofstelle",
        "building_features": ["historische bausubstanz"],
        "outbuildings": ["Scheune", "Stall", "Tenne"],
        "special_features": ["Alleinlage"],
        "exclusion_flags": [],
        "listing_status": ListingStatus.ACTIVE,
        "verification_status": VerificationStatus.VERIFIED,
    }
    defaults.update(kwargs)
    prop = Property(**defaults)
    if session is not None:
        session.add(prop)
        session.flush()
    return prop


def attach_source(
    session: Session,
    prop: Property,
    source: Source,
    *,
    contact_kind: str | None = None,
    contact_detail: str | None = None,
    source_date: datetime | None = None,
    is_best: bool = True,
    url: str | None = None,
) -> PropertySource:
    link = PropertySource(
        property_id=prop.id,
        source_id=source.id,
        url=url or f"https://example.invalid/{prop.public_id}/{source.key}",
        role=source.role,
        is_best=is_best,
        contact_kind=contact_kind,
        contact_detail=contact_detail,
        source_date=source_date,
    )
    session.add(link)
    session.flush()
    session.refresh(prop)
    return link


def days_ago(now: datetime, days: float) -> datetime:
    return now - timedelta(days=days)
