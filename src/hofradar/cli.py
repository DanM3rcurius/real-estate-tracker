"""Command line entry point.

The web UI is the product, but a scheduled run has to work headlessly, and a
one-off "what does the database think right now?" should not require a browser.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from hofradar.config import load_config, reload_config
from hofradar.db.session import database_url, init_db, session_scope


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def cmd_initdb(args: argparse.Namespace) -> int:
    init_db()
    from hofradar.sources import sync_sources_to_db

    cfg = load_config()
    with session_scope() as session:
        sources = sync_sources_to_db(session, cfg.sources)
    print(f"database ready at {database_url()}")
    print(f"registered {len(sources)} sources")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    init_db()
    uvicorn.run(
        "hofradar.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        factory=False,
    )
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from hofradar.pipeline import run_pipeline

    init_db()
    cfg = reload_config()
    run = asyncio.run(
        run_pipeline(
            cfg.profile,
            trigger=args.trigger,
            source_keys=args.sources,
            dry_run=args.dry_run,
        )
    )
    print(
        f"run {run.id}: {run.status} | seen={run.listings_seen} new={run.properties_new} "
        f"updated={run.properties_updated} price_changes={run.price_changes} "
        f"removed={run.removed}"
    )
    return 0 if run.status == "ok" else 1


def cmd_report(args: argparse.Namespace) -> int:
    from hofradar.report import build_report, render_markdown
    from hofradar.scoring import rescore_all

    init_db()
    cfg = reload_config()
    with session_scope() as session:
        # Scores and cost estimates are profile-keyed, so a report for a
        # profile nothing has been scored against yet would print "noch nicht
        # berechnet" everywhere. Bring the cache up to date first.
        rescore_all(session, cfg.profile, only_dirty=True)
        data = build_report(session, cfg.profile)
        markdown = render_markdown(data)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(markdown, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(markdown)
    return 0


def cmd_rescore(args: argparse.Namespace) -> int:
    from hofradar.scoring import rescore_all

    init_db()
    cfg = reload_config()
    profile = cfg.profile
    if args.air_km is not None:
        profile = profile.model_copy(
            update={"radius": profile.radius.model_copy(update={"air_km_max": args.air_km})}
        )
    if args.budget is not None:
        profile = profile.model_copy(
            update={
                "budget": profile.budget.model_copy(update={"total_budget_max": args.budget})
            }
        )
    with session_scope() as session:
        count = rescore_all(session, profile, only_dirty=False)
    print(f"profile {profile.profile_hash}: rescored {count} properties")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    from hofradar.config import SourceConfig
    from hofradar.geo import locate
    from hofradar.lifecycle import ingest
    from hofradar.normalize import normalize_listing
    from hofradar.sources import get_adapter, sync_sources_to_db

    init_db()
    cfg = reload_config()

    async def _run() -> int:
        count = 0
        with session_scope() as session:
            sources = {s.key: s for s in sync_sources_to_db(session, cfg.sources)}
            source = sources["csv_import"]
            adapter = get_adapter(
                SourceConfig(
                    key="csv_import",
                    name="CSV Import",
                    role="primary",
                    adapter="csv",
                    options={"path": args.path},
                )
            )
            async for raw in adapter.discover(cfg.profile, cfg.keywords):
                listing = normalize_listing(raw, cfg.keywords)
                # Route imported rows too - an unrouted property is capped
                # below the shortlist threshold by design, so skipping this
                # would quietly bury everything a CSV brings in.
                geo = await locate(session, listing, cfg.profile)
                ingest(session, listing, source=source, geo=geo)
                count += 1
        return count

    count = asyncio.run(_run())
    print(f"imported {count} listings from {args.path}")
    return 0


def cmd_repair(args: argparse.Namespace) -> int:
    """Undo the phantom removals of GitHub issue #2. Dry run unless --apply."""
    from hofradar.lifecycle import repair_phantom_removals
    from hofradar.sources import get_adapter, sync_sources_to_db

    init_db()
    cfg = reload_config()
    with session_scope() as session:
        sources = sync_sources_to_db(session, cfg.sources)
        non_reporting = {s.key for s in sources if not get_adapter(s).enumerates}
        report = repair_phantom_removals(
            session, non_reporting_source_keys=non_reporting, dry_run=not args.apply
        )
    print(report.summary())
    if report.dry_run and report.total:
        print("\nnothing was written. re-run with --apply to restore them.")
    return 0


def cmd_hash_password(args: argparse.Namespace) -> int:
    """Print a PBKDF2 hash to put in HOFRADAR_PASSWORD_HASH.

    Preferred over HOFRADAR_PASSWORD: the hash can sit in a compose file, a
    Fly secret or a shell history without being the password itself.
    """
    import getpass

    from hofradar.web.auth import hash_password

    password = args.password or getpass.getpass("Passwort: ")
    if not password:
        print("no password given", file=sys.stderr)
        return 1
    if not args.password:
        again = getpass.getpass("Wiederholen: ")
        if again != password:
            print("passwords do not match", file=sys.stderr)
            return 1
    print(hash_password(password))
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = reload_config()
    profile = cfg.profile
    print(
        json.dumps(
            {
                "profile_hash": profile.profile_hash,
                "center": profile.center.model_dump(),
                "air_km_max": profile.radius.air_km_max,
                "driving_soft_km": profile.radius.effective_driving_soft,
                "driving_hard_km": profile.radius.effective_driving_hard,
                "total_budget_max": profile.budget.total_budget_max,
                "purchase_target_max": profile.budget.effective_purchase_target_max,
                "purchase_negotiation_max": profile.budget.effective_purchase_negotiation_max,
                "purchase_hard_max": profile.budget.effective_purchase_hard_max,
                "total_exceptional_max": profile.budget.effective_total_exceptional_max,
                "total_hard_max": profile.budget.effective_total_hard_max,
                "sources_enabled": [s.key for s in cfg.sources if s.enabled],
                "sources_disabled": [s.key for s in cfg.sources if not s.enabled],
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hofradar", description="Hofradar")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db", help="create tables and register sources").set_defaults(
        func=cmd_initdb
    )

    p_serve = sub.add_parser("serve", help="run the web UI")
    p_serve.add_argument("--host", default="0.0.0.0")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--reload", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    p_run = sub.add_parser("run", help="execute the search pipeline once")
    p_run.add_argument("--sources", nargs="*", help="limit to these source keys")
    p_run.add_argument("--trigger", default="cli")
    p_run.add_argument("--dry-run", action="store_true")
    p_run.set_defaults(func=cmd_run)

    p_report = sub.add_parser("report", help="render the weekly report")
    p_report.add_argument("--out", help="write markdown to this path")
    p_report.set_defaults(func=cmd_report)

    p_rescore = sub.add_parser("rescore", help="recompute scores, optionally with new sliders")
    p_rescore.add_argument("--air-km", type=float, help="override the air radius")
    p_rescore.add_argument("--budget", type=float, help="override the total budget")
    p_rescore.set_defaults(func=cmd_rescore)

    p_import = sub.add_parser("import", help="import listings from a CSV file")
    p_import.add_argument("path")
    p_import.set_defaults(func=cmd_import)

    p_repair = sub.add_parser(
        "repair-removals",
        help="restore properties wrongly marked removed (GitHub issue #2)",
    )
    p_repair.add_argument(
        "--apply", action="store_true", help="actually write; default is a dry run"
    )
    p_repair.set_defaults(func=cmd_repair)

    p_hash = sub.add_parser(
        "hash-password", help="print a PBKDF2 hash for HOFRADAR_PASSWORD_HASH"
    )
    p_hash.add_argument(
        "--password",
        help="read the password from the command line instead of prompting "
        "(it will land in your shell history)",
    )
    p_hash.set_defaults(func=cmd_hash_password)

    sub.add_parser("config", help="show the resolved search profile").set_defaults(
        func=cmd_config
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
