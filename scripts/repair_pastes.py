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

Counts what it did not repair in three separate buckets - already complete,
nothing recoverable, no stored text - because one summed "skipped" said
nothing about which of them a run had actually hit. A chrome title (issue #10)
counts as recoverable: canonical_title is never NULL, so a wrong one can only
be spotted by comparing it with what a re-parse produces.

Dry run unless you pass --apply.
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import select

from hofradar.config import reload_config
from hofradar.contracts import PAGE_KIND_LISTING
from hofradar.db.migrate import ensure_schema
from hofradar.db.models import Observation, Property, Source
from hofradar.db.session import session_scope
from hofradar.geo import locate
from hofradar.lifecycle import ingest
from hofradar.normalize import normalize_listing
from hofradar.sources import get_adapter

MANUAL_KEY = "manual"

#: The facts a re-parse can fill in on a property that is missing them. Every
#: one of them is NULL-able on ``Property``, so "missing" is unambiguous here -
#: unlike the title, which is handled separately below.
RECOVERABLE_FIELDS: tuple[str, ...] = (
    "price",
    "land_sqm",
    "living_sqm",
    "year_built",
    "town",
    "postcode",
)


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
        repaired = already_complete = no_stored_text = nothing_recoverable = 0
        for prop in properties:
            observation = session.scalars(
                select(Observation)
                .where(Observation.property_id == prop.id, Observation.source_id == source.id)
                .order_by(Observation.scraped_at.desc())
            ).first()
            if observation is None or not observation.description:
                no_stored_text += 1
                continue

            # ``ingest_text``, not the plain-text helper underneath it: a paste
            # can be HTML, and reading HTML as plain text makes the first line
            # of markup the title. The adapter branches on that; we must not
            # reach past it.
            raw = adapter.ingest_text(observation.url, observation.description)
            listing = normalize_listing(raw, cfg.keywords)
            label = (prop.canonical_title or "")[:44]

            if listing.page_kind != PAGE_KIND_LISTING:
                # A paste that was never a listing (a portal's Merkliste,
                # GitHub issue #10) cannot be repaired into one - ingest
                # refuses it now. What to do with the row it already produced
                # is a separate question.
                print(f"{prop.public_id}  {label:44}  ! {listing.page_kind} page, not repairable")
                nothing_recoverable += 1
                continue

            gains = [
                name
                for name in RECOVERABLE_FIELDS
                if getattr(prop, name, None) is None and getattr(listing, name, None) is not None
            ]
            # The title is the odd one out: ``canonical_title`` is never NULL
            # (a paste that produced none got "Objekt <Ort>"), and a chrome
            # title lifted off portal markup is a *wrong* value rather than a
            # missing one - so a re-parse that produces a different title is a
            # gain too. ingest overwrites it, this source being a verifying one.
            if listing.title and listing.title != prop.canonical_title:
                gains.append("title")

            if not gains:
                if any(getattr(prop, name, None) is None for name in RECOVERABLE_FIELDS):
                    nothing_recoverable += 1
                else:
                    already_complete += 1
                continue

            print(f"{prop.public_id}  {label:44}  + {', '.join(gains)}")
            if apply:
                geo = await locate(session, listing, cfg.profile)
                ingest(session, listing, source=source, geo=geo)
            repaired += 1

        if not apply:
            session.rollback()

        # Three different reasons, printed as three numbers: one summed
        # "skipped" hid whether the pastes were fine, unreadable or simply
        # beyond this script's reach.
        print(
            f"\n{repaired} to repair, {already_complete} already complete, "
            f"{nothing_recoverable} with nothing recoverable, "
            f"{no_stored_text} with no stored text."
        )
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
