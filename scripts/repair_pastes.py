#!/usr/bin/env python3
"""Re-parse hand-pasted listings that were stored before the paste box was fixed.

Ingest writes the Observation before the Property, so the text you pasted is
still on record even though the fields it should have produced are empty. This
re-reads that text through the fixed parser and re-ingests it under the
listing's ORIGINAL url - so dedupe matches the existing row and updates it in
place. Nothing is duplicated and nothing new is created.

Reads the stored text back through the adapter's own ``ingest_text``, so an
HTML paste is parsed as HTML rather than having its first line of markup
become the title.

Geocodes before re-ingesting, exactly as the paste box does: a town with no
coordinates still has no distance, and the scorer caps an unrouted property
below the shortlist gate - so skipping it would leave the listing just as
invisible as before, with better-looking fields.

Dry run unless you pass --apply.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from hofradar.config import reload_config
from hofradar.db.migrate import ensure_schema
from hofradar.db.models import Observation, Property, Source
from hofradar.db.session import session_scope
from hofradar.geo import locate
from hofradar.lifecycle import ingest
from hofradar.normalize import normalize_listing
from hofradar.sources import get_adapter

MANUAL_KEY = "manual"


async def run(apply: bool) -> int:
    ensure_schema()
    cfg = reload_config()

    with session_scope() as session:
        source = session.scalar(select(Source).where(Source.key == MANUAL_KEY))
        if source is None:
            print("no 'manual' source in this database - nothing was pasted here.")
            return 0

        properties = session.scalars(
            select(Property).join(Observation, Observation.property_id == Property.id)
            .where(Observation.source_id == source.id)
            .distinct()
        ).all()

        adapter = get_adapter(source)
        repaired = skipped = 0
        for prop in properties:
            observation = session.scalars(
                select(Observation)
                .where(Observation.property_id == prop.id, Observation.source_id == source.id)
                .order_by(Observation.scraped_at.desc())
            ).first()
            if observation is None or not observation.description:
                skipped += 1
                continue

            # ``ingest_text``, not the plain-text helper underneath it: a paste
            # can be HTML, and reading HTML as plain text makes the first line
            # of markup the title. The adapter branches on that; we must not
            # reach past it.
            raw = adapter.ingest_text(observation.url, observation.description)
            listing = normalize_listing(raw, cfg.keywords)

            gains = [
                name
                for name in ("price", "land_sqm", "living_sqm", "year_built", "town", "postcode")
                if getattr(prop, name, None) is None and getattr(listing, name, None) is not None
            ]
            if not gains:
                skipped += 1
                continue

            print(f"{prop.public_id}  {(prop.canonical_title or '')[:44]:44}  + {', '.join(gains)}")
            if apply:
                geo = await locate(session, listing, cfg.profile)
                ingest(session, listing, source=source, geo=geo)
            repaired += 1

        if not apply:
            session.rollback()

        print(f"\n{repaired} to repair, {skipped} already fine or with no stored text.")
        if repaired and not apply:
            print("nothing was written. re-run with --apply.")
        elif repaired:
            print("repaired in place - no duplicates created.")
            print("run `hofradar rescore` next so the radar picks up the new values.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write; default is a dry run")
    args = parser.parse_args()
    return asyncio.run(run(args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
