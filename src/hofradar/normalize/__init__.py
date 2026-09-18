"""German real-estate text -> typed, canonical facts.

Public surface per ``docs/MODULE_API.md``. Everything else in this package
(the individual parser modules) is an implementation detail other packages
must not import directly.
"""

from __future__ import annotations

from hofradar.normalize.dates import parse_german_date
from hofradar.normalize.features import FeatureExtraction, classify_property_type, extract_features
from hofradar.normalize.listing import normalize_listing
from hofradar.normalize.location import LocationParts, parse_location
from hofradar.normalize.numbers import is_rent_price, parse_area, parse_german_number, parse_price
from hofradar.normalize.text import normalize_text, text_hash

__all__ = [
    "normalize_listing",
    "is_rent_price",
    "parse_price",
    "parse_area",
    "parse_german_number",
    "parse_german_date",
    "normalize_text",
    "text_hash",
    "extract_features",
    "classify_property_type",
    "parse_location",
    "LocationParts",
    "FeatureExtraction",
]
