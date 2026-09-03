"""Bulk import from a CSV/TSV spreadsheet export.

Column names are matched case-insensitively and umlaut-folded, so both a
German export ("Kaufpreis", "Grundstück", "Wohnfläche") and an English one
("price", "land", "living") land on the same RawListing fields. Unknown
columns are kept in ``extra`` rather than silently dropped - a spreadsheet a
human curated by hand often carries a useful note column.
"""

from __future__ import annotations

import csv
import io
import logging
import re
from collections.abc import AsyncIterator, Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hofradar.config import KeywordConfig, SearchProfile
from hofradar.contracts import RawListing
from hofradar.sources.base import SourceAdapter

logger = logging.getLogger(__name__)

_UMLAUT_FOLD = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})

#: Folded header -> RawListing field name. Add synonyms here, not in the code.
_HEADER_MAP: dict[str, str] = {
    "preis": "price_raw",
    "price": "price_raw",
    "kaufpreis": "price_raw",
    "verkaufspreis": "price_raw",
    "grundstueck": "land_raw",
    "grundstuecksflaeche": "land_raw",
    "grundstuecksgroesse": "land_raw",
    "land": "land_raw",
    "grundstueck_qm": "land_raw",
    "wohnflaeche": "living_raw",
    "wohnflaeche_qm": "living_raw",
    "living": "living_raw",
    "living_area": "living_raw",
    "nutzflaeche": "usable_raw",
    "usable": "usable_raw",
    "usable_area": "usable_raw",
    "zimmer": "rooms_raw",
    "rooms": "rooms_raw",
    "baujahr": "year_raw",
    "year": "year_raw",
    "year_built": "year_raw",
    "ort": "town",
    "town": "town",
    "city": "town",
    "plz": "postcode",
    "postcode": "postcode",
    "postleitzahl": "postcode",
    "zip": "postcode",
    "postal_code": "postcode",
    "titel": "title",
    "title": "title",
    "beschreibung": "description",
    "description": "description",
    "url": "url",
    "link": "url",
    "quelle_url": "url",
    "externe_id": "external_id",
    "external_id": "external_id",
    "id": "external_id",
    "adresse": "location_raw",
    "address": "location_raw",
    "lage": "location_raw",
    "location": "location_raw",
    "strasse": "location_raw",
    "street": "location_raw",
    "bilder": "image_urls",
    "images": "image_urls",
    "image_urls": "image_urls",
    "fotos": "image_urls",
    "kontakt": "contact_name",
    "contact": "contact_name",
    "kontaktname": "contact_name",
    "datum": "source_date_raw",
    "date": "source_date_raw",
}

_IMAGE_SPLIT_RE = re.compile(r"[;|]+|\s+(?=https?://)")


def _fold(header: str) -> str:
    return header.strip().lower().translate(_UMLAUT_FOLD)


def _split_image_urls(value: str) -> list[str]:
    parts = [p.strip() for p in _IMAGE_SPLIT_RE.split(value) if p.strip()]
    return parts or [value.strip()]


def rows_to_listings(rows: Iterable[Mapping[str, Any]], *, source_key: str) -> list[RawListing]:
    listings: list[RawListing] = []
    for row in rows:
        mapped: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        for raw_key, raw_val in row.items():
            if raw_key is None:
                continue
            value = (raw_val or "").strip() if isinstance(raw_val, str) else raw_val
            if not value:
                continue
            field = _HEADER_MAP.get(_fold(raw_key))
            if field is None:
                extra[raw_key.strip()] = value
            elif field == "image_urls":
                mapped["image_urls"] = _split_image_urls(value)
            else:
                mapped[field] = value

        url = mapped.pop("url", None)
        if not url:
            logger.warning("csv: skipping row without a url column: %r", dict(row))
            continue

        listings.append(
            RawListing(
                source_key=source_key,
                url=url,
                fetched_at=datetime.now(UTC),
                extra=extra,
                **mapped,
            )
        )
    return listings


def _sniff_delimiter(sample: str) -> str:
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        return dialect.delimiter
    except csv.Error:
        return ","


def parse_csv_text(text: str, *, source_key: str) -> list[RawListing]:
    """Parse a whole CSV/TSV document (auto-detecting the delimiter) into RawListings."""
    text = text.lstrip("﻿")
    if not text.strip():
        return []
    sample = "\n".join(text.splitlines()[:5])
    delimiter = _sniff_delimiter(sample)
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    return rows_to_listings(reader, source_key=source_key)


class CsvAdapter(SourceAdapter):
    """Reads ``options.path`` (or ``options.paths``, a list) and emits one RawListing per row."""

    async def discover(
        self, profile: SearchProfile, keywords: KeywordConfig
    ) -> AsyncIterator[RawListing]:
        paths: list[str] = list(self.options.get("paths") or [])
        single = self.options.get("path")
        if single:
            paths.append(single)
        if not paths:
            logger.info("%s: no CSV path configured (options.path/paths) - nothing to import", self.key)
            return

        for path in paths:
            try:
                text = Path(path).read_text(encoding="utf-8-sig")
            except OSError as exc:
                logger.warning("%s: could not read %s: %s", self.key, path, exc)
                continue
            try:
                listings = parse_csv_text(text, source_key=self.key)
            except csv.Error as exc:
                logger.warning("%s: could not parse %s as CSV/TSV: %s", self.key, path, exc)
                continue
            for listing in listings:
                yield listing

    async def fetch_detail(self, url: str) -> RawListing | None:
        # A CSV row already carries the full record the user chose to export;
        # there is no separate detail page to enrich it from.
        return None
