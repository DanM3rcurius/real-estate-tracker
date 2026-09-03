"""The weekly pipeline.

The whole point of the ordering below is that expensive and error-prone work
happens *last*, on the smallest possible set:

    discovery -> crawl -> normalize -> dedupe -> geo filter -> verify
              -> change detection -> cost -> LLM review -> rank -> report

A crawler that finds 10.000 pages must not become 10.000 LLM calls. By the time
the language model is involved we are down to a hundred candidates that already
survived deterministic filtering, and it is only asked to do the thing it is
actually good at: reading prose and spotting risks.

Each stage records its outcome on the ``SearchRun`` row, so a failed run is
diagnosable after the fact instead of being a silent gap in the history.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from hofradar.config import KeywordConfig, SearchProfile, load_config
from hofradar.db.enums import RunStage, SourceRole
from hofradar.db.models import Property, PropertySource, SearchRun, Source
from hofradar.db.session import session_scope

log = logging.getLogger(__name__)

#: Sources whose silence proves nothing - never used to mark a listing removed.
NON_VERIFYING_ROLES = {SourceRole.DISCOVERY}


class PipelineError(RuntimeError):
    pass


def _log_stage(session: Session, run: SearchRun, stage: RunStage, **fields: Any) -> None:
    run.stage = stage
    entry = {"stage": str(stage), "at": datetime.now(UTC).isoformat(), **fields}
    run.log = [*(run.log or []), entry]
    session.add(run)
    session.flush()
    log.info("stage=%s %s", stage, fields)


async def run_pipeline(
    profile: SearchProfile | None = None,
    *,
    trigger: str = "manual",
    source_keys: list[str] | None = None,
    dry_run: bool = False,
    stale_after_days: int | None = None,
) -> SearchRun:
    """Execute one full search run. Returns the persisted :class:`SearchRun`."""
    from hofradar.costmodel import estimate_costs  # noqa: F401  (used via scoring)
    from hofradar.dedupe import find_duplicate  # noqa: F401  (used via lifecycle)
    from hofradar.geo import locate, within_air_radius
    from hofradar.lifecycle import ImplausibleAbsence, apply_stale_rules, ingest, mark_missing
    from hofradar.normalize import normalize_listing
    from hofradar.report import build_report, render_html, render_markdown
    from hofradar.scoring import rescore_all
    from hofradar.sources import get_adapter, sync_sources_to_db

    cfg = load_config()
    profile = profile or cfg.profile
    keywords: KeywordConfig = cfg.keywords

    with session_scope() as session:
        run = SearchRun(trigger=trigger, profile_hash=profile.profile_hash, status="running")
        session.add(run)
        session.flush()
        run_id = run.id

        try:
            # -- 1. which sources ------------------------------------------ #
            sync_sources_to_db(session, cfg.sources)
            stmt = select(Source).where(Source.enabled.is_(True))
            if source_keys:
                stmt = select(Source).where(Source.key.in_(source_keys))
            sources = list(session.scalars(stmt))
            _log_stage(
                session, run, RunStage.DISCOVERY, sources=[s.key for s in sources]
            )
            run.sources_run = len(sources)

            # -- 2. crawl + normalize + geo + ingest ----------------------- #
            seen_by_source: dict[int, set[int]] = defaultdict(set)
            #: Did this source list its whole inventory, without error and
            #: without truncation? Absence detection is skipped unless it did.
            enumeration_complete: dict[int, bool] = {}
            listings_seen = 0
            new_count = 0
            updated_count = 0
            price_changes = 0

            for source in sources:
                adapter = get_adapter(source)
                try:
                    async for raw in adapter.discover(profile, keywords):
                        listings_seen += 1
                        listing = normalize_listing(raw, keywords)

                        # Record that the source still carries this URL BEFORE
                        # any filter can skip it. A listing we choose not to
                        # ingest is not a listing the source stopped offering,
                        # and conflating the two removes live properties.
                        known_id = _known_property_id(session, source.id, listing.url)
                        if known_id is not None:
                            seen_by_source[source.id].add(known_id)

                        # Cheap deterministic reject before any geocoding call.
                        if listing.exclusion_flags and not listing.building_features:
                            continue

                        geo = await locate(session, listing, profile)
                        if geo.distance_air_km is not None and not within_air_radius(
                            geo.distance_air_km, profile
                        ):
                            # Outside the radius: remember nothing, spend nothing.
                            continue

                        if dry_run:
                            continue

                        prop, change = ingest(
                            session, listing, run_id=run_id, source=source, geo=geo
                        )
                        seen_by_source[source.id].add(prop.id)
                        if change.kind == "first_seen":
                            new_count += 1
                        else:
                            updated_count += 1
                        if change.kind == "price_change":
                            price_changes += 1
                    source.last_run_at = datetime.now(UTC)
                    source.consecutive_failures = 0
                    source.last_error = None
                    enumeration_complete[source.id] = adapter.can_prove_absence
                except Exception as exc:  # one bad source must not kill the run
                    log.exception("source %s failed", source.key)
                    # The crawl died part-way, so what we have is a partial
                    # view. Absence detection must not run on it - otherwise a
                    # transient 403 silently deletes this source's history.
                    enumeration_complete[source.id] = False
                    source.consecutive_failures += 1
                    source.last_error = f"{type(exc).__name__}: {exc}"
                    _log_stage(
                        session, run, RunStage.CRAWL, source=source.key, error=str(exc)
                    )
                session.flush()

            run.listings_seen = listings_seen
            run.properties_new = new_count
            run.properties_updated = updated_count
            run.price_changes = price_changes
            _log_stage(
                session,
                run,
                RunStage.CRAWL,
                listings=listings_seen,
                new=new_count,
                updated=updated_count,
            )

            # -- 3. disappearance detection -------------------------------- #
            removed = 0
            if not dry_run:
                for source in sources:
                    if source.role in NON_VERIFYING_ROLES:
                        continue  # a discovery source's silence is not evidence
                    try:
                        changes = mark_missing(
                            session,
                            seen_by_source.get(source.id, set()),
                            source=source,
                            run_id=run_id,
                            enumeration_complete=enumeration_complete.get(source.id, False),
                        )
                    except ImplausibleAbsence as exc:
                        # One source's absences are not believed - recorded as
                        # a source failure, same posture as a crawl exception
                        # above, so the run continues for every other source.
                        log.error("absence detection refused for %s: %s", source.key, exc)
                        source.last_error = str(exc)
                        _log_stage(
                            session, run, RunStage.CHANGE_DETECTION,
                            source=source.key, error=str(exc),
                        )
                        continue
                    removed += sum(1 for c in changes if c.kind == "removed")
                # Sources that will never mention a listing again must not put
                # their properties on the "we stopped hearing" clock - nobody
                # was ever going to speak.
                non_reporting = {
                    s.id for s in sources if not get_adapter(s).enumerates
                }
                apply_stale_rules(
                    session,
                    stale_after_days=(
                        stale_after_days
                        if stale_after_days is not None
                        else profile.gates.stale_after_days
                    ),
                    unverified_stale_after_days=profile.gates.unverified_stale_after_days,
                    non_reporting_source_ids=non_reporting,
                    run_id=run_id,
                )
            run.removed = removed
            _log_stage(session, run, RunStage.CHANGE_DETECTION, removed=removed)

            # -- 4. cost + score ------------------------------------------- #
            scored = rescore_all(session, profile, only_dirty=False)
            _log_stage(session, run, RunStage.RANK, scored=scored)

            # -- 5. LLM deep review of the survivors only ------------------ #
            reviewed = await _llm_review(session, profile, run_id=run_id)
            if reviewed:
                _log_stage(session, run, RunStage.LLM, reviewed=reviewed)
                rescore_all(session, profile, only_dirty=False)

            # -- 6. report -------------------------------------------------- #
            data = build_report(session, profile, run_id=run_id)
            render_markdown(data)
            render_html(data)
            _log_stage(session, run, RunStage.REPORT)

            run.status = "ok"
        except Exception as exc:
            run.status = "failed"
            run.error = f"{type(exc).__name__}: {exc}"
            log.exception("pipeline run failed")
            raise PipelineError(str(exc)) from exc
        finally:
            run.finished_at = datetime.now(UTC)
            session.add(run)

        session.flush()
        session.expunge(run)
        return run


def _known_property_id(session: Session, source_id: int, url: str) -> int | None:
    """The property this source already has on record under ``url``, if any.

    Used to mark a listing as still-offered before the pipeline's own filters
    get a chance to skip it.
    """
    return session.scalar(
        select(PropertySource.property_id).where(
            PropertySource.source_id == source_id, PropertySource.url == url
        )
    )


async def _llm_review(session: Session, profile: SearchProfile, *, run_id: int | None) -> int:
    """Ask the model about the top candidates only. Silently skipped when unconfigured."""
    from hofradar.llm import LLMUnavailable, review_properties
    from hofradar.scoring import ranked_properties

    try:
        candidates = [
            prop
            for prop, _score in ranked_properties(
                session, profile, limit=profile.gates.llm_review_size
            )
        ]
        if not candidates:
            return 0
        return await review_properties(session, candidates, profile)
    except LLMUnavailable as exc:
        log.info("LLM review skipped: %s", exc)
        return 0


def run_pipeline_sync(profile: SearchProfile | None = None, **kwargs: Any) -> SearchRun:
    return asyncio.run(run_pipeline(profile, **kwargs))


def properties_count(session: Session) -> int:
    return session.scalar(select(Property.id).limit(1)) is not None  # type: ignore[return-value]
