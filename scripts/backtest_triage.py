#!/usr/bin/env python3
"""Ask Jev about the properties you already judged, and see where the threshold bites.

The triage threshold (``gates.triage_reject_min_probability``) shipped as a
guess. This database holds the ground you can check it against: the farms on
the Merkliste and under watch, the ones you rejected or archived by hand, and
the rentals the regex already typed. This script asks the model about each
of them and prints, per human verdict and per threshold, how many the triage
would reject or flag - so "0 of 12 Merkliste farms rejected, 19 of 31 archived
ones" is a number you can argue with, instead of 0.85 being an article of
faith.

It does NOT pretend those groups are labels. "Archived" means you did not want
to see it again, which covers too-far and too-dear as well as flat-and-rented.
The table keeps the groups apart for that reason; read it, do not sum it.

Dry run unless you pass --call (each call costs money, little of it). With
--store, every verdict is also written to ``Property.evidence["triage"]``
for rows that have none - the backfill the crawl cannot do for a pasted or
CSV-imported row that never re-crawls - and the profile is rescored so the
gate applies. Evidence is the one thing this writes; the same precedent as
``hofradar.llm.review`` writing its summary. It never creates or deletes a
row (invariant 1) and never touches a number.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select

from hofradar.config import GateConfig, reload_config
from hofradar.contracts import NormalizedListing
from hofradar.db.enums import HIDDEN_USER_STATES, PriceType
from hofradar.db.migrate import ensure_schema
from hofradar.db.models import Observation, Property
from hofradar.db.session import session_scope
from hofradar.triage import (
    DWELLING_FLAT,
    OFFER_RENT,
    TRIAGE_EVIDENCE_KEY,
    JevTriage,
    TriageVerdict,
    decide,
)

DEFAULT_THRESHOLDS = (0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 0.95)
MAX_CONCURRENCY = 4

#: The human verdicts, in the order the table prints them. Every property
#: lands in exactly one group; the first match wins, so a rental on the
#: Merkliste reads as Merkliste - that is the row worth looking at.
GROUP_MERKLISTE = "merkliste"
GROUP_WATCHED = "beobachtet"
GROUP_REJECTED = "abgelehnt"
GROUP_ARCHIVED = "archiviert"
GROUP_RENT_REGEX = "miete_regex"
GROUP_UNJUDGED = "unbewertet"
GROUP_ORDER = (
    GROUP_MERKLISTE,
    GROUP_WATCHED,
    GROUP_REJECTED,
    GROUP_ARCHIVED,
    GROUP_RENT_REGEX,
    GROUP_UNJUDGED,
)
WATCH_STATES = frozenset({"watch", "contacted"})


def label_group(prop: Property) -> str:
    """Which human verdict, if any, this row already carries."""
    if getattr(prop, "shortlisted_at", None) is not None:
        return GROUP_MERKLISTE
    state = getattr(prop, "user_state", None) or ""
    if state in WATCH_STATES:
        return GROUP_WATCHED
    if state == "rejected":
        return GROUP_REJECTED
    if state in HIDDEN_USER_STATES:
        return GROUP_ARCHIVED
    if str(getattr(prop, "price_type", "") or "").lower() == PriceType.RENT:
        return GROUP_RENT_REGEX
    return GROUP_UNJUDGED


def listing_from_property(prop: Property, observation: Observation | None) -> NormalizedListing:
    """The listing as the crawl would have handed it to the triage.

    The raw price string lives on the observation (the row keeps the parsed
    number); the description falls back from the freshest observation to
    the property's own, so a pasted exposé is asked about in full.
    """
    return NormalizedListing(
        source_key="backtest",
        url=getattr(observation, "url", None) or prop.public_id,
        title=prop.canonical_title,
        description=(observation.description if observation else None) or prop.description,
        price_raw=(observation.price_raw if observation else None),
        rooms=prop.rooms,
        living_sqm=prop.living_sqm,
        land_sqm=prop.land_sqm,
        town=prop.town,
        outbuildings=list(prop.outbuildings or []),
    )


@dataclass(slots=True)
class Row:
    public_id: str
    group: str
    has_substance: bool
    verdict: TriageVerdict


def sweep(rows: list[Row], thresholds: tuple[float, ...]) -> dict[float, dict[str, Counter[str]]]:
    """threshold -> group -> {"n", "reject_miete", "reject_wohnung", "flag"}."""
    table: dict[float, dict[str, Counter[str]]] = {}
    for threshold in thresholds:
        gates = GateConfig(triage_reject_min_probability=threshold)
        per_group: dict[str, Counter[str]] = defaultdict(Counter)
        for row in rows:
            decision = decide(row.verdict, gates, has_substance=row.has_substance)
            bucket = per_group[row.group]
            bucket["n"] += 1
            if decision.reject_reason == OFFER_RENT:
                bucket["reject_miete"] += 1
            elif decision.reject_reason == DWELLING_FLAT:
                bucket["reject_wohnung"] += 1
            elif decision.flags:
                bucket["flag"] += 1
        table[threshold] = dict(per_group)
    return table


def render_table(table: dict[float, dict[str, Counter[str]]], current: float) -> str:
    """One line per threshold, one column per human verdict: rejected/flagged of n."""
    groups = [g for g in GROUP_ORDER if any(g in per for per in table.values())]
    header = f"{'schwelle':>9} | " + " | ".join(f"{g:>22}" for g in groups)
    lines = [header, "-" * len(header)]
    for threshold, per_group in table.items():
        cells = []
        for group in groups:
            bucket = per_group.get(group, Counter())
            rejected = bucket["reject_miete"] + bucket["reject_wohnung"]
            cells.append(
                f"{rejected:>3} abgel. {bucket['flag']:>3} Flag / {bucket['n']:>3}".rjust(22)
            )
        marker = " <- konfiguriert" if abs(threshold - current) < 1e-9 else ""
        lines.append(f"{threshold:>9.2f} | " + " | ".join(cells) + marker)
    return "\n".join(lines)


def _latest_observation(session, prop: Property) -> Observation | None:
    return session.scalars(
        select(Observation)
        .where(Observation.property_id == prop.id)
        .order_by(Observation.scraped_at.desc())
    ).first()


async def run(
    *,
    call: bool,
    store: bool,
    limit: int | None,
    thresholds: tuple[float, ...],
    json_path: Path | None,
) -> int:
    ensure_schema()
    cfg = reload_config()
    current = cfg.profile.gates.triage_reject_min_probability

    with session_scope() as session:
        stmt = select(Property).order_by(Property.id)
        if limit:
            stmt = stmt.limit(limit)
        properties = session.scalars(stmt).all()
        groups = Counter(label_group(p) for p in properties)
        print(f"{len(properties)} Objekte in der Datenbank, nach menschlichem Urteil:")
        for group in GROUP_ORDER:
            if groups[group]:
                print(f"  {group:<12} {groups[group]:>5}")
        if not call:
            print(
                "\nTrockenlauf: keine Anfrage gestellt. Mit --call fragt das Skript Jev pro Objekt "
                "einmal (drei Fragen, ein Aufruf); mit --store landet die Antwort als "
                "evidence['triage'] auf Objekten, die noch keine haben."
            )
            return 0

        triage = JevTriage.from_env()  # raises TriageUnavailable without a key - loudly
        semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

        async def _one(prop: Property) -> Row | None:
            async with semaphore:
                listing = listing_from_property(prop, _latest_observation(session, prop))
                verdict = await triage.classify(listing)
            if verdict is None:
                return None
            return Row(
                public_id=prop.public_id,
                group=label_group(prop),
                has_substance=bool(prop.outbuildings),
                verdict=verdict,
            )

        results = await asyncio.gather(*(_one(p) for p in properties))
        await triage.aclose()
        rows = [r for r in results if r is not None]
        print(
            f"\n{triage.asked} gefragt, {triage.failed} fehlgeschlagen, Modell {triage.model}."
        )
        if not rows:
            print("Keine Antworten - nichts zu vergleichen.")
            return 1

        print()
        print(render_table(sweep(rows, thresholds), current))
        print(
            "\nLesart: 'abgel.' = wuerde bei dieser Schwelle aus dem Radar fallen, "
            "'Flag' = nur markiert. Die Merkliste-Spalte sollte 0 abgelehnt zeigen."
        )

        if json_path is not None:
            payload: list[dict[str, Any]] = [
                {"public_id": r.public_id, "group": r.group, **r.verdict.to_evidence()}
                for r in rows
            ]
            json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
            print(f"Antworten pro Objekt: {json_path}")

        if store:
            by_id = {r.public_id: r for r in rows}
            written = 0
            for prop in properties:
                row = by_id.get(prop.public_id)
                if row is None or TRIAGE_EVIDENCE_KEY in (prop.evidence or {}):
                    continue
                evidence = dict(prop.evidence or {})
                evidence[TRIAGE_EVIDENCE_KEY] = row.verdict.to_evidence()
                prop.evidence = evidence
                session.add(prop)
                written += 1
            session.flush()
            from hofradar.scoring import rescore_all

            scored = rescore_all(session, cfg.profile, only_dirty=False)
            print(f"\n{written} Objekte mit Triage-Evidenz versehen, {scored} neu bewertet.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--call", action="store_true", help="actually ask Jev (costs money)")
    parser.add_argument(
        "--store",
        action="store_true",
        help="write each verdict to evidence['triage'] where none exists, then rescore",
    )
    parser.add_argument("--limit", type=int, default=None, help="only the first N properties")
    parser.add_argument(
        "--thresholds",
        default=",".join(str(t) for t in DEFAULT_THRESHOLDS),
        help="comma-separated probabilities to sweep",
    )
    parser.add_argument("--json", type=Path, default=None, help="dump per-property verdicts")
    args = parser.parse_args()
    if args.store and not args.call:
        parser.error("--store needs --call: there is nothing to store from a dry run")
    thresholds = tuple(float(t) for t in args.thresholds.split(",") if t.strip())
    return asyncio.run(
        run(
            call=args.call,
            store=args.store,
            limit=args.limit,
            thresholds=thresholds,
            json_path=args.json,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
