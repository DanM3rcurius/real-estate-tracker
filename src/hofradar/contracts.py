"""Stage-to-stage contracts.

The pipeline is a chain of pure-ish transformations:

    RawListing -> NormalizedListing -> (dedupe) -> Property row
                                    -> (geo)    -> distances
                                    -> (cost)   -> CostEstimate
                                    -> (score)  -> ScoreResult

Every stage takes and returns one of the dataclasses below. Nothing else is
shared between packages, so the modules can be developed and tested in
isolation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Evidence:
    """Why the system believes a fact."""

    source: str
    url: str | None = None
    quote: str | None = None
    confidence: float = 0.5
    observed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "url": self.url,
            "quote": self.quote,
            "confidence": self.confidence,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
        }


@dataclass(slots=True)
class RawListing:
    """What a source adapter yields. Deliberately unopinionated and stringy."""

    source_key: str
    url: str
    title: str | None = None
    description: str | None = None
    price_raw: str | None = None
    land_raw: str | None = None
    living_raw: str | None = None
    usable_raw: str | None = None
    rooms_raw: str | None = None
    year_raw: str | None = None
    location_raw: str | None = None
    postcode: str | None = None
    town: str | None = None
    external_id: str | None = None
    image_urls: list[str] = field(default_factory=list)
    contact_name: str | None = None
    contact_kind: str | None = None
    contact_detail: str | None = None
    source_date_raw: str | None = None
    listing_visible: bool = True
    http_status: int | None = None
    fetched_at: datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class NormalizedListing:
    """Post-normalisation: typed values, canonical tags, evidence attached."""

    source_key: str
    url: str
    title: str | None = None
    description: str | None = None

    price: float | None = None
    price_type: str = "unknown"
    price_raw: str | None = None

    land_sqm: float | None = None
    living_sqm: float | None = None
    usable_sqm: float | None = None
    rooms: float | None = None
    year_built: int | None = None

    street: str | None = None
    postcode: str | None = None
    town: str | None = None
    district: str | None = None

    property_type: str | None = None
    building_features: list[str] = field(default_factory=list)
    outbuildings: list[str] = field(default_factory=list)
    special_features: list[str] = field(default_factory=list)
    exclusion_flags: list[str] = field(default_factory=list)
    hidden_signals: list[str] = field(default_factory=list)

    is_foreclosure: bool = False
    is_monument: bool = False
    is_private_seller: bool = False
    is_off_market_signal: bool = False

    external_id: str | None = None
    image_urls: list[str] = field(default_factory=list)
    image_hashes: list[str] = field(default_factory=list)
    contact_name: str | None = None
    contact_kind: str | None = None
    contact_detail: str | None = None

    source_date: datetime | None = None
    listing_visible: bool = True
    http_status: int | None = None
    fetched_at: datetime | None = None

    text_hash: str | None = None
    evidence: dict[str, dict[str, Any]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def add_evidence(self, field_name: str, ev: Evidence) -> None:
        self.evidence[field_name] = ev.to_dict()


@dataclass(slots=True)
class GeoResult:
    lat: float | None = None
    lon: float | None = None
    precision: str = "none"  # exact | street | town | postcode | none
    distance_air_km: float | None = None
    distance_driving_km: float | None = None
    distance_driving_minutes: float | None = None
    display_name: str | None = None
    provider: str | None = None
    #: True only when a real road route was computed. Never infer from air distance.
    routed: bool = False


@dataclass(slots=True)
class CostResult:
    purchase_price: float | None = None
    acquisition_costs: float = 0.0
    renovation_low: float = 0.0
    renovation_mid: float = 0.0
    renovation_high: float = 0.0
    immediate_capex: float = 0.0
    total_low: float = 0.0
    total_mid: float = 0.0
    total_high: float = 0.0
    renovation_tier: str = "unknown"
    #: "observed" or "inferred" - see hofradar.costmodel.renovation_evidence.
    renovation_evidence: str = "inferred"
    breakdown: dict[str, float] = field(default_factory=dict)
    assumptions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ScoreResult:
    fit_score: float = 0.0
    deal_score: float = 0.0
    hidden_score: float = 0.0
    freshness_score: float = 0.0
    confidence_score: float = 0.0
    final_score: float = 0.0
    capital_risk: str = "low"
    rejected: bool = False
    reject_reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    breakdown: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class DuplicateVerdict:
    is_duplicate: bool
    confidence: float
    reasons: list[str] = field(default_factory=list)
    matched_property_id: int | None = None


@dataclass(slots=True)
class ChangeResult:
    """What the lifecycle stage decided about one property this run."""

    kind: str  # ChangeKind
    old_status: str | None = None
    new_status: str | None = None
    old_price: float | None = None
    new_price: float | None = None
    delta_abs: float | None = None
    delta_pct: float | None = None
    detail: str | None = None
