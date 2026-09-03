"""Controlled vocabularies shared by every stage of the pipeline.

These are stored as plain strings in the database (portable across SQLite and
Postgres, and survives a value being added without a migration).
"""

from __future__ import annotations

from enum import StrEnum


class SourceRole(StrEnum):
    """Priority class of a source.

    A DISCOVERY source may *find* a property but can never confirm that the
    listing is still live, nor can it establish freshness.
    """

    PRIMARY = "primary"      # Class A: portal / broker / seller original
    LOCAL = "local"          # Class B: newspaper, Gemeindeblatt, Amtsblatt
    DISCOVERY = "discovery"  # Class C: search engines, aggregators, caches, archives


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"    # only ever seen via a discovery source
    VERIFIED = "verified"        # a primary source was fetched and parsed
    CONFLICTING = "conflicting"  # primary sources disagree on core facts
    FAILED = "failed"            # primary source fetch failed / 404 / gone


class ListingStatus(StrEnum):
    DISCOVERED = "discovered"
    VERIFIED = "verified"
    ACTIVE = "active"
    PRICE_CHANGED = "price_changed"
    STALE = "stale"
    REMOVED = "removed"
    SOLD = "sold"
    FORECLOSURE = "foreclosure"
    OFF_MARKET_SIGNAL = "off_market_signal"


class ChangeKind(StrEnum):
    """What the weekly diff reports. NEVER report FIRST_SEEN for a known property."""

    FIRST_SEEN = "first_seen"
    REACTIVATED = "reactivated"
    PRICE_CHANGE = "price_change"
    DESCRIPTION_CHANGE = "description_change"
    SOURCE_CHANGE = "source_change"
    NEW_DOCUMENT = "new_document"
    NEW_CONTACT = "new_contact"
    STATUS_CHANGE = "status_change"
    REMOVED = "removed"
    STALE = "stale"
    UNCHANGED = "unchanged"


class PriceType(StrEnum):
    ASKING = "asking"            # a concrete number
    NEGOTIABLE = "negotiable"    # "VB" / "Verhandlungsbasis"
    ON_REQUEST = "on_request"    # "Preis auf Anfrage"
    AUCTION_MIN = "auction_min"  # Verkehrswert / Mindestgebot (ZVG)
    UNKNOWN = "unknown"


class RenovationTier(StrEnum):
    LIGHT = "light"
    MEDIUM = "medium"
    HEAVY = "heavy"
    COMPLETE = "complete"
    UNKNOWN = "unknown"


class ConditionVerdict(StrEnum):
    """Result of reconciling text claims against photo evidence."""

    GOOD = "good"
    FAIR = "fair"
    BAD = "bad"
    CONFLICTING = "conflicting"  # text and image disagree - never silently pick one
    UNKNOWN = "unknown"


class DocumentKind(StrEnum):
    AMTSBLATT = "amtsblatt"
    GEMEINDEBLATT = "gemeindeblatt"
    ZVG_EXPOSE = "zvg_expose"
    EXPOSE = "expose"
    NEWSPAPER = "newspaper"
    OTHER = "other"


class CapitalRisk(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    EXTREME = "extreme"


class RunStage(StrEnum):
    DISCOVERY = "discovery"
    CRAWL = "crawl"
    NORMALIZE = "normalize"
    DEDUPE = "dedupe"
    GEO = "geo"
    VERIFY = "verify"
    CHANGE_DETECTION = "change_detection"
    COST = "cost"
    LLM = "llm"
    RANK = "rank"
    REPORT = "report"
