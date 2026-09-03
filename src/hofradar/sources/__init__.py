"""Pluggable source layer: adapters that turn a portal, feed, spreadsheet, or
foreclosure register into a stream of RawListings.

Per docs/MODULE_API.md (``hofradar.sources``), the pipeline only ever imports
``ADAPTERS``, ``get_adapter`` and ``SourceAdapter`` from here.
``sync_sources_to_db`` is the one write path from config/sources.yaml into
the ``sources`` table and is exported alongside them; the exception types are
exported too so callers can catch them without reaching into submodules.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from hofradar.config import SourceConfig
from hofradar.db.models import Source
from hofradar.sources.adapters.csv_adapter import CsvAdapter
from hofradar.sources.adapters.denkmalboerse import DenkmalboerseAdapter
from hofradar.sources.adapters.generic_rss import GenericRssAdapter
from hofradar.sources.adapters.generic_sitemap import GenericSitemapAdapter
from hofradar.sources.adapters.immoscout import ImmoscoutAdapter
from hofradar.sources.adapters.immowelt import ImmoweltAdapter
from hofradar.sources.adapters.kleinanzeigen import KleinanzeigenAdapter
from hofradar.sources.adapters.manual import ManualAdapter
from hofradar.sources.adapters.ovbimmo import OvbimmoAdapter
from hofradar.sources.adapters.pdf_bulletin import PdfBulletinAdapter
from hofradar.sources.adapters.web_search import WebSearchAdapter
from hofradar.sources.adapters.zvg import ZvgAdapter
from hofradar.sources.base import PoliteClient, SourceAdapter
from hofradar.sources.exceptions import (
    BotDefenseDetected,
    NotSupported,
    RobotsDisallowed,
    SourceDiscoveryError,
    SourceError,
)

logger = logging.getLogger(__name__)

#: adapter type name (SourceConfig.adapter, or Source.config["adapter"] once
#: synced) -> the class that implements it. Keys match config/sources.yaml's
#: ``adapter:`` field exactly.
ADAPTERS: dict[str, type[SourceAdapter]] = {
    "manual": ManualAdapter,
    "csv": CsvAdapter,
    "denkmalboerse": DenkmalboerseAdapter,
    "ovbimmo": OvbimmoAdapter,
    "generic_rss": GenericRssAdapter,
    "generic_sitemap": GenericSitemapAdapter,
    "zvg": ZvgAdapter,
    "pdf_bulletin": PdfBulletinAdapter,
    "kleinanzeigen": KleinanzeigenAdapter,
    "immoscout": ImmoscoutAdapter,
    "immowelt": ImmoweltAdapter,
    "web_search": WebSearchAdapter,
}


def _adapter_name(source: Source | SourceConfig) -> str:
    if isinstance(source, SourceConfig):
        return source.adapter
    # A Source ORM row has no `adapter` column of its own - sync_sources_to_db
    # writes it into the JSON `config` blob alongside the adapter options.
    config = getattr(source, "config", None) or {}
    name = config.get("adapter")
    if not name:
        raise ValueError(
            f"source {getattr(source, 'key', '?')!r} has no adapter recorded in its "
            "config; run sync_sources_to_db to populate it from config/sources.yaml"
        )
    return name


def get_adapter(source: Source | SourceConfig) -> SourceAdapter:
    """Instantiate the adapter class this source is configured to use."""
    name = _adapter_name(source)
    cls = ADAPTERS.get(name)
    if cls is None:
        known = ", ".join(sorted(ADAPTERS))
        raise ValueError(f"unknown adapter {name!r} for source {source.key!r}; known adapters: {known}")
    return cls(source)


def sync_sources_to_db(session: Session, configs: Iterable[SourceConfig]) -> list[Source]:
    """Upsert config/sources.yaml into the ``sources`` table.

    Matches existing rows on ``key``. Updates name/role/base_url/region/
    reliability/enabled/rate limits/respect_robots/listing_ttl_days/notes/
    config. Never touches ``last_run_at``, ``last_error`` or
    ``consecutive_failures`` - that is the pipeline's runtime history, and a
    config reload must not reset it.
    """
    rows: list[Source] = []
    for cfg in configs:
        row = session.scalars(select(Source).where(Source.key == cfg.key)).one_or_none()
        if row is None:
            row = Source(key=cfg.key)
            session.add(row)
            logger.info("sources: registering new source %r", cfg.key)

        row.name = cfg.name
        row.role = cfg.role
        row.base_url = cfg.base_url
        row.region = cfg.region
        row.reliability = cfg.reliability
        row.enabled = cfg.enabled
        row.rate_limit_seconds = cfg.rate_limit_seconds
        row.respect_robots = cfg.respect_robots
        row.listing_ttl_days = cfg.listing_ttl_days
        row.notes = cfg.notes
        row.config = {"adapter": cfg.adapter, "options": cfg.options}
        rows.append(row)

    session.flush()
    return rows


__all__ = [
    "ADAPTERS",
    "BotDefenseDetected",
    "NotSupported",
    "PoliteClient",
    "RobotsDisallowed",
    "SourceAdapter",
    "SourceDiscoveryError",
    "SourceError",
    "get_adapter",
    "sync_sources_to_db",
]
