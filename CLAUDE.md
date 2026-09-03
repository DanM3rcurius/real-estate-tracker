# Hofradar — working notes for Claude Code

## What this is

A research platform with memory for finding Bavarian farmsteads. Not a scraper.
The database remembering what it has seen is the product; the crawling is
plumbing.

Read `docs/DECISIONS.md` before changing architecture and `docs/MODULE_API.md`
before changing a package's public surface.

## Invariants — do not break these

1. **`hofradar.lifecycle.ingest` is the only writer of `Property` rows.** It
   writes the `Observation` first, always.
2. **Never report a known property as new.** `ChangeKind.FIRST_SEEN` is only for
   a property that did not exist. Reappearance is `REACTIVATED`.
3. **`distance_air_km` and `distance_driving_km` never substitute for each
   other.** Unknown road distance is `None` and must be rendered as such.
4. **Source role gates authority.** A `discovery` source may not set
   `verification_status=verified`, `last_verified` or `source_date`, and its
   silence may not mark a listing removed.
5. **Scores never live on `Property`.** They go in `scores`, keyed by
   `profile_hash`, because the sliders move.
6. **The LLM may not write a number.** Advisory fields only.
7. **No bot-defence evasion**, ever. If a source blocks us, we record it and stop.

## Layout

```
config/          search DNA, scoring, keywords, source registry (YAML)
src/hofradar/
  config.py      SearchProfile - the adjustable parameters, profile_hash
  contracts.py   stage-to-stage dataclasses
  db/            models.py, enums.py, session.py
  normalize/     German text -> typed facts + evidence
  dedupe/        fingerprint, compare, merge
  lifecycle/     ingest, change detection, status transitions
  geo/           Nominatim geocoding, OSRM routing, haversine, gazetteer
  costmodel/     Bavarian side costs + component-based renovation model
  scoring/       fit / deal / hidden / freshness / confidence + gates
  sources/       SourceAdapter base + adapters/
  llm/           the last stage, advisory only
  pipeline/      the orchestrator
  report/        weekly digest (max 10 entries, everything else counted)
  web/           FastAPI + Jinja + HTMX + Leaflet
tests/           mirrors src/, one directory per package
```

## Conventions

- Python 3.11+, `from __future__ import annotations`, full type hints, 100 cols.
- Module docstrings explain **why**, not what.
- No magic numbers in function bodies — module constants or config.
- UI copy is German. Code, identifiers, comments and docstrings are English.
- Emoji belong in UI copy and report output only, never in identifiers.

## Running things

```bash
pip install -e ".[dev,pdf,images]"
hofradar init-db && hofradar serve
PYTHONPATH=src python -m pytest -q
ruff check src tests
```

Tests must never hit the network. Use `respx` for HTTP and `HOFRADAR_OFFLINE=1`
for the geo gazetteer path.
