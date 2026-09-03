"""The persistence layer.

Design rules that the rest of the codebase depends on
====================================================

1. ``Property`` holds the *canonical, current best-known* facts about a real
   physical place. It is never overwritten blindly - every write goes through
   the lifecycle module, which records an ``Observation`` first.

2. ``Observation`` is append-only. One row per (source, property, crawl). Price
   history, status history and change detection are all derived from it. This
   is what turns "search again every week" into a memory.

3. Scores are **not** columns on ``Property``. The user can move the distance
   and budget sliders at any time, so every score is a pure function of
   (facts, SearchProfile) and is cached in ``Score`` keyed by ``profile_hash``.

4. Every load-bearing fact carries evidence. ``Property.evidence`` maps a field
   name to ``{source, url, quote, confidence, observed_at}`` so the UI can
   always answer "why does the system believe this?".
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from hofradar.db.enums import (
    ChangeKind,
    ListingStatus,
    PriceType,
    SourceRole,
    VerificationStatus,
)
from hofradar.db.session import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


# --------------------------------------------------------------------------- #
# Sources
# --------------------------------------------------------------------------- #


class Source(Base, TimestampMixin):
    """A place listings come from. Its ``role`` decides what it is allowed to prove."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16), default=SourceRole.DISCOVERY)
    base_url: Mapped[str | None] = mapped_column(String(500))
    region: Mapped[str | None] = mapped_column(String(120))

    #: 0.0-1.0. Feeds the reliability term of the confidence score.
    reliability: Mapped[float] = mapped_column(Float, default=0.5)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Per-source politeness. Adapters must honour this.
    rate_limit_seconds: Mapped[float] = mapped_column(Float, default=2.0)
    respect_robots: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Fixed advertising window (days) for sources that sell one, e.g. a
    #: newspaper's fortnight. None means the source has no such window and its
    #: silence, once allowed to prove anything at all, always means REMOVED.
    listing_ttl_days: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    notes: Mapped[str | None] = mapped_column(Text)
    config: Mapped[dict] = mapped_column(JSON, default=dict)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)

    observations: Mapped[list[Observation]] = relationship(back_populates="source")

    @property
    def can_verify(self) -> bool:
        """Only primary and local sources may confirm that a listing is live."""
        return self.role in (SourceRole.PRIMARY, SourceRole.LOCAL)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Source {self.key} role={self.role}>"


# --------------------------------------------------------------------------- #
# Properties
# --------------------------------------------------------------------------- #


class Property(Base, TimestampMixin):
    """A physical place, not a listing. Five listings of one farm collapse here."""

    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Stable public identifier, safe to put in reports and URLs.
    public_id: Mapped[str] = mapped_column(String(24), unique=True, index=True)

    canonical_title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)

    # -- location ---------------------------------------------------------- #
    street: Mapped[str | None] = mapped_column(String(200))
    postcode: Mapped[str | None] = mapped_column(String(16), index=True)
    town: Mapped[str | None] = mapped_column(String(160), index=True)
    district: Mapped[str | None] = mapped_column(String(160))
    state: Mapped[str | None] = mapped_column(String(80), default="Bayern")
    country: Mapped[str] = mapped_column(String(2), default="DE")
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    #: How precise the geocode is: 'exact' | 'street' | 'town' | 'postcode' | 'none'
    geo_precision: Mapped[str] = mapped_column(String(16), default="none")

    #: Straight-line km from the configured search centre. Never conflate with driving.
    distance_air_km: Mapped[float | None] = mapped_column(Float, index=True)
    #: Real road distance. NULL means "not yet measured", never "assume air distance".
    distance_driving_km: Mapped[float | None] = mapped_column(Float)
    distance_driving_minutes: Mapped[float | None] = mapped_column(Float)

    # -- money ------------------------------------------------------------- #
    price: Mapped[float | None] = mapped_column(Float, index=True)
    price_type: Mapped[str] = mapped_column(String(16), default=PriceType.UNKNOWN)
    price_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    price_first: Mapped[float | None] = mapped_column(Float)
    price_reduction_count: Mapped[int] = mapped_column(Integer, default=0)

    # -- physical ---------------------------------------------------------- #
    land_sqm: Mapped[float | None] = mapped_column(Float, index=True)
    living_sqm: Mapped[float | None] = mapped_column(Float)
    usable_sqm: Mapped[float | None] = mapped_column(Float)
    rooms: Mapped[float | None] = mapped_column(Float)
    year_built: Mapped[int | None] = mapped_column(Integer)
    condition: Mapped[str | None] = mapped_column(String(32))
    property_type: Mapped[str | None] = mapped_column(String(80), index=True)

    #: Normalised tag lists produced by the feature extractor.
    building_features: Mapped[list] = mapped_column(JSON, default=list)
    outbuildings: Mapped[list] = mapped_column(JSON, default=list)
    special_features: Mapped[list] = mapped_column(JSON, default=list)
    exclusion_flags: Mapped[list] = mapped_column(JSON, default=list)

    #: field name -> {source, url, quote, confidence, observed_at}
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)

    # -- lifecycle --------------------------------------------------------- #
    listing_status: Mapped[str] = mapped_column(
        String(24), default=ListingStatus.DISCOVERED, index=True
    )
    verification_status: Mapped[str] = mapped_column(
        String(16), default=VerificationStatus.UNVERIFIED, index=True
    )
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_verified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Best evidence of when the *seller* last touched the listing (not our crawl date).
    source_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    is_foreclosure: Mapped[bool] = mapped_column(Boolean, default=False)
    is_monument: Mapped[bool] = mapped_column(Boolean, default=False)
    is_private_seller: Mapped[bool] = mapped_column(Boolean, default=False)
    is_off_market_signal: Mapped[bool] = mapped_column(Boolean, default=False)

    #: Deduplication key computed by hofradar.dedupe.fingerprint.
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    merged_into_id: Mapped[int | None] = mapped_column(
        ForeignKey("properties.id", ondelete="SET NULL")
    )

    #: Free-form flags from the LLM / manual triage stage.
    llm_summary: Mapped[str | None] = mapped_column(Text)
    llm_risks: Mapped[list] = mapped_column(JSON, default=list)
    llm_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Human triage - survives every re-run and every profile change.
    user_state: Mapped[str | None] = mapped_column(String(24), index=True)
    user_note: Mapped[str | None] = mapped_column(Text)

    observations: Mapped[list[Observation]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
    property_sources: Mapped[list[PropertySource]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
    price_history: Mapped[list[PriceHistory]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
    status_history: Mapped[list[StatusHistory]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
    images: Mapped[list[Image]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
    documents: Mapped[list[Document]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
    scores: Mapped[list[Score]] = relationship(
        back_populates="property", cascade="all, delete-orphan"
    )
    cost_estimate: Mapped[CostEstimate | None] = relationship(
        back_populates="property", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        Index("ix_properties_geo", "lat", "lon"),
        Index("ix_properties_active", "listing_status", "distance_air_km"),
    )

    @property
    def source_count(self) -> int:
        return len(self.property_sources)

    @property
    def is_alive(self) -> bool:
        return self.listing_status not in (
            ListingStatus.REMOVED,
            ListingStatus.SOLD,
        )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Property {self.public_id} {self.town} {self.price}>"


class PropertySource(Base, TimestampMixin):
    """Join row: which sources have advertised this property, and how.

    Breadth of offering is itself a signal - a farm on six portals is not hidden.
    """

    __tablename__ = "property_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))

    url: Mapped[str] = mapped_column(String(1000))
    external_id: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(16), default=SourceRole.DISCOVERY)
    is_primary_source: Mapped[bool] = mapped_column(Boolean, default=False)
    #: Best URL to show a human. Set on exactly one row per property.
    is_best: Mapped[bool] = mapped_column(Boolean, default=False)

    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_listing_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    #: Seller-side publication date if the source exposes one.
    source_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    contact_name: Mapped[str | None] = mapped_column(String(200))
    contact_kind: Mapped[str | None] = mapped_column(String(32))  # broker | private | authority
    contact_detail: Mapped[str | None] = mapped_column(String(500))

    property: Mapped[Property] = relationship(back_populates="property_sources")
    source: Mapped[Source] = relationship()

    __table_args__ = (UniqueConstraint("source_id", "url", name="uq_property_source_url"),)


class Observation(Base):
    """Append-only crawl record. Never updated, never deleted."""

    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    run_id: Mapped[int | None] = mapped_column(ForeignKey("search_runs.id", ondelete="SET NULL"))

    url: Mapped[str] = mapped_column(String(1000))
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    title: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[float | None] = mapped_column(Float)
    price_type: Mapped[str | None] = mapped_column(String(16))
    price_raw: Mapped[str | None] = mapped_column(String(200))
    land_sqm: Mapped[float | None] = mapped_column(Float)
    living_sqm: Mapped[float | None] = mapped_column(Float)
    usable_sqm: Mapped[float | None] = mapped_column(Float)
    year_built: Mapped[int | None] = mapped_column(Integer)
    town: Mapped[str | None] = mapped_column(String(160))
    postcode: Mapped[str | None] = mapped_column(String(16))

    #: False means the fetch proved the listing is gone (404/410/"nicht mehr verfuegbar").
    listing_visible: Mapped[bool] = mapped_column(Boolean, default=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    #: Stable hash of the normalised listing text - drives DESCRIPTION_CHANGE.
    listing_text_hash: Mapped[str | None] = mapped_column(String(64))
    image_hashes: Mapped[list] = mapped_column(JSON, default=list)
    #: Seller-side date parsed out of the page, if any.
    source_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict] = mapped_column(JSON, default=dict)

    property: Mapped[Property] = relationship(back_populates="observations")
    source: Mapped[Source] = relationship(back_populates="observations")

    __table_args__ = (Index("ix_observations_prop_time", "property_id", "scraped_at"),)


class PriceHistory(Base):
    __tablename__ = "price_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), index=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    old_price: Mapped[float | None] = mapped_column(Float)
    new_price: Mapped[float | None] = mapped_column(Float)
    delta_abs: Mapped[float | None] = mapped_column(Float)
    delta_pct: Mapped[float | None] = mapped_column(Float)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))

    property: Mapped[Property] = relationship(back_populates="price_history")


class StatusHistory(Base):
    __tablename__ = "status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), index=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    old_status: Mapped[str | None] = mapped_column(String(24))
    new_status: Mapped[str] = mapped_column(String(24))
    change_kind: Mapped[str] = mapped_column(String(32), default=ChangeKind.STATUS_CHANGE)
    detail: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("search_runs.id", ondelete="SET NULL"))

    property: Mapped[Property] = relationship(back_populates="status_history")


class Image(Base, TimestampMixin):
    __tablename__ = "images"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(String(1000))
    local_path: Mapped[str | None] = mapped_column(String(500))
    phash: Mapped[str | None] = mapped_column(String(32), index=True)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    #: Vision-extracted features. Advisory only - never overwrites text facts.
    visual_features: Mapped[dict] = mapped_column(JSON, default=dict)

    property: Mapped[Property] = relationship(back_populates="images")

    __table_args__ = (UniqueConstraint("property_id", "url", name="uq_image_property_url"),)


class Document(Base, TimestampMixin):
    """PDF / Amtsblatt evidence, kept down to the page and the matched sentence."""

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int | None] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))

    kind: Mapped[str] = mapped_column(String(32))
    document_url: Mapped[str] = mapped_column(String(1000))
    title: Mapped[str | None] = mapped_column(String(500))
    document_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    issue: Mapped[str | None] = mapped_column(String(80))  # e.g. "KW 34"
    page_number: Mapped[int | None] = mapped_column(Integer)
    matched_text: Mapped[str | None] = mapped_column(Text)
    local_path: Mapped[str | None] = mapped_column(String(500))
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False)

    property: Mapped[Property] = relationship(back_populates="documents")


class VerificationEvent(Base):
    """Audit trail for 'we actually fetched a primary source and it said X'."""

    __tablename__ = "verification_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id", ondelete="SET NULL"))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    url: Mapped[str] = mapped_column(String(1000))
    outcome: Mapped[str] = mapped_column(String(16))  # verified | failed | gone | conflicting
    http_status: Mapped[int | None] = mapped_column(Integer)
    detail: Mapped[str | None] = mapped_column(Text)


# --------------------------------------------------------------------------- #
# Derived / cached
# --------------------------------------------------------------------------- #


class CostEstimate(Base, TimestampMixin):
    """Total-cost model output. Depends on facts only, not on the user's sliders."""

    __tablename__ = "cost_estimates"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), unique=True, index=True
    )

    purchase_price: Mapped[float | None] = mapped_column(Float)
    acquisition_costs: Mapped[float | None] = mapped_column(Float)
    renovation_low: Mapped[float | None] = mapped_column(Float)
    renovation_mid: Mapped[float | None] = mapped_column(Float)
    renovation_high: Mapped[float | None] = mapped_column(Float)
    immediate_capex: Mapped[float | None] = mapped_column(Float)

    total_low: Mapped[float | None] = mapped_column(Float)
    total_mid: Mapped[float | None] = mapped_column(Float, index=True)
    total_high: Mapped[float | None] = mapped_column(Float)

    renovation_tier: Mapped[str | None] = mapped_column(String(16))
    #: {"house": .., "roof": .., "outbuildings": .., "utilities": .., "contingency": ..}
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)
    assumptions: Mapped[list] = mapped_column(JSON, default=list)

    property: Mapped[Property] = relationship(back_populates="cost_estimate")


class Score(Base, TimestampMixin):
    """Cached score for one property under one search profile.

    ``profile_hash`` is the stable hash of the SearchProfile the user is using.
    Move a slider -> new hash -> scores recomputed. Nothing is ever stale-wrong.
    """

    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(
        ForeignKey("properties.id", ondelete="CASCADE"), index=True
    )
    profile_hash: Mapped[str] = mapped_column(String(32), index=True)

    fit_score: Mapped[float] = mapped_column(Float, default=0.0)
    deal_score: Mapped[float] = mapped_column(Float, default=0.0)
    hidden_score: Mapped[float] = mapped_column(Float, default=0.0)
    freshness_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    final_score: Mapped[float] = mapped_column(Float, default=0.0, index=True)

    capital_risk: Mapped[str | None] = mapped_column(String(16))
    rejected: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    reject_reasons: Mapped[list] = mapped_column(JSON, default=list)
    flags: Mapped[list] = mapped_column(JSON, default=list)
    #: Per-component point breakdown, so the UI can explain every number.
    breakdown: Mapped[dict] = mapped_column(JSON, default=dict)

    property: Mapped[Property] = relationship(back_populates="scores")

    __table_args__ = (
        UniqueConstraint("property_id", "profile_hash", name="uq_score_property_profile"),
    )


class GeoCache(Base):
    """Geocoding and routing results. Free upstream APIs are slow and rate limited."""

    __tablename__ = "geo_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))  # geocode | route
    key: Mapped[str] = mapped_column(String(500), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("kind", "key", name="uq_geo_cache_kind_key"),)


class SearchProfileRecord(Base, TimestampMixin):
    """A saved, user-editable set of search parameters (the adjustable sliders)."""

    __tablename__ = "search_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    profile_hash: Mapped[str] = mapped_column(String(32), index=True)
    #: Full serialised SearchProfile (see hofradar.config.SearchProfile).
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class SearchRun(Base):
    """One execution of the weekly pipeline."""

    __tablename__ = "search_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")
    trigger: Mapped[str] = mapped_column(String(24), default="manual")
    profile_hash: Mapped[str | None] = mapped_column(String(32))
    stage: Mapped[str | None] = mapped_column(String(24))

    sources_run: Mapped[int] = mapped_column(Integer, default=0)
    listings_seen: Mapped[int] = mapped_column(Integer, default=0)
    properties_new: Mapped[int] = mapped_column(Integer, default=0)
    properties_updated: Mapped[int] = mapped_column(Integer, default=0)
    price_changes: Mapped[int] = mapped_column(Integer, default=0)
    removed: Mapped[int] = mapped_column(Integer, default=0)
    duplicates_merged: Mapped[int] = mapped_column(Integer, default=0)

    log: Mapped[list] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)


class ReportRecord(Base):
    __tablename__ = "reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("search_runs.id", ondelete="SET NULL"))
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    week_label: Mapped[str] = mapped_column(String(32))  # e.g. "KW 35 / 2026"
    period_start: Mapped[Date | None] = mapped_column(Date)
    profile_hash: Mapped[str | None] = mapped_column(String(32))
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    markdown: Mapped[str | None] = mapped_column(Text)
    html: Mapped[str | None] = mapped_column(Text)
