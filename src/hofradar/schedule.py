"""The weekly heartbeat.

Deliberately a separate process from the web app: a long crawl must never block
the UI, and a crashed crawl must never take the UI down with it.
"""

from __future__ import annotations

import asyncio
import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from hofradar.config import reload_config
from hofradar.db.migrate import ensure_schema

log = logging.getLogger("hofradar.schedule")

DEFAULT_CRON = os.environ.get("HOFRADAR_SCHEDULE_CRON", "0 6 * * 1")  # Mondays 06:00


async def _job() -> None:
    from hofradar.pipeline import run_pipeline

    cfg = reload_config()
    log.info("scheduled run starting (profile %s)", cfg.profile.profile_hash)
    try:
        run = await run_pipeline(cfg.profile, trigger="schedule")
        log.info(
            "run %s finished: %s new=%s updated=%s price_changes=%s removed=%s",
            run.id,
            run.status,
            run.properties_new,
            run.properties_updated,
            run.price_changes,
            run.removed,
        )
    except Exception:
        log.exception("scheduled run failed")


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    ensure_schema()
    scheduler = AsyncIOScheduler(timezone=os.environ.get("TZ", "Europe/Berlin"))
    scheduler.add_job(_job, CronTrigger.from_crontab(DEFAULT_CRON), id="weekly")
    scheduler.start()
    log.info("scheduler started with cron %r", DEFAULT_CRON)
    if os.environ.get("HOFRADAR_RUN_ON_START") == "1":
        await _job()
    await asyncio.Event().wait()


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(main())
