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
4b. **Absence needs a complete enumeration, not just permission.** Being
   allowed to verify is not the same as having listed everything. A source
   only removes a listing when `SourceAdapter.can_prove_absence` is true -
   it enumerates, this run finished without error, and nothing was truncated.
   A paste box, a CSV import, an RSS feed, a capped crawl and a source that
   threw a 403 all prove nothing by their silence. (GitHub issue #2.)
5. **Scores never live on `Property`.** They go in `scores`, keyed by
   `profile_hash`, because the sliders move.
6. **The LLM may not write a number.** Advisory fields only.
7. **No bot-defence evasion**, ever. If a source blocks us, we record it and stop.
8. **The password gate is opt-in but never half-installed.** With no
   password configured the middleware is absent entirely; with one, every
   path outside `PUBLIC_PATHS` needs a valid session, and `/healthz` tells
   an anonymous caller nothing about the database.

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
  triage/        System One (Jev) second opinion: typed questions, thresholded by gates
  pipeline/      the orchestrator
  report/        weekly digest (max 10 entries, everything else counted)
  web/           FastAPI + Jinja + HTMX + Leaflet
  web/auth.py    one password, one signed cookie (see docs/DEPLOY.md)
tests/           mirrors src/, one directory per package
```

## Conventions

- Python 3.11+, `from __future__ import annotations`, full type hints, 100 cols.
- Module docstrings explain **why**, not what.
- No magic numbers in function bodies — module constants or config.
- UI copy is German. Code, identifiers, comments and docstrings are English.
- Emoji belong in UI copy and report output only, never in identifiers.

## Running things

After editing anything in `config/`, run `python scripts/sync_config_defaults.py`
so the copies bundled into the package stay identical. CI fails if they drift.

`hofradar init-db` migrates the schema before registering sources, so a
database from an older revision is brought up to date on every boot. Add a
migration with `alembic -c alembic.ini revision --autogenerate -m "..."`; it
lands in `src/hofradar/migrations/versions/` so the installed wheel carries it.

```bash
pip install -e ".[dev,pdf,images]"
hofradar init-db && hofradar serve
hofradar migrate --check          # pending schema work? (exit 1 if so)
PYTHONPATH=src python -m pytest -q
ruff check src tests
```

Tests must never hit the network: they mock every outbound call with `respx`
and assert on the request that *would* have been made.

Do **not** run the suite with `HOFRADAR_OFFLINE=1` in the environment. That
variable short-circuits the very geo code paths those tests exercise, and 12
tests fail for a reason that has nothing to do with the code. Only the tests
that want the gazetteer set it, and they set it themselves.

## Known ground, as of the #7/#3 session

Things a fresh session would otherwise rediscover the hard way.

**The recurring bug in this codebase is silence that looks like success.**
Issues #2, #3, #7 and #10 are all one shape: a stage produces a confident
result from an input it should have rejected or a fact it quietly dropped, and
nothing errors. When something here goes wrong, suspect a missing warning
before a wrong calculation. Anything that drops a load-bearing fact gets a
`NormalizedListing.warnings` entry and a place in the UI - see decision 18.
A fetched page now has to prove it is a listing before `ingest` will remember
it (`page_kind`, `NotAListing`, decision 19) - a fact-count gate cannot, because
a portal's search page yields facts scraped off several adverts at once.

**The suite cannot see schema drift on its own.** Every fixture builds its
database from the models with `create_all()`, where a missing migration is
invisible. `tests/db/test_migrations.py` builds one from the migrations alone
and compares - that is the test that would have caught #7, so do not weaken it.

**CI has never run.** All 44 workflow runs to date fail in 2-4 seconds with no
step logs and a 404 on the job log: the job never reaches a runner (Actions
billing / repo settings, not the code). Green locally is currently the only
verification that exists. Do not read a red check on a PR as a real failure
without opening the run first.

**`pipeline/runner.py` has no `commit()` at all.** The whole run is one
`session_scope()` transaction, so the `SearchRun(status="running")` row and
every `_log_stage` entry stay invisible to other connections until the run
finishes - which is why `/runs` shows no progress and why killing a run mid-way
loses all of it. `POST /api/run` also has no guard against starting a second
concurrent run, and `_execute` swallows every exception with a bare `return`.
Fixing the visibility means deciding what a crashed run should leave behind;
that is a design call, not a patch. The visible symptom while a run holds
SQLite's write lock: every web write (a rescore for a slider position with no
scores yet, a paste, a Merkliste click) waits out `SQLITE_BUSY_TIMEOUT_MS`
and fails with "database is locked". The radar used to 500 on that because
the failed flush left the session in pending-rollback; `web/query._rescore`
now rolls back and renders the last stored scores with a notice naming the
crawl. The triage stage makes runs longer (one HTTP call per listing), so the
window is wider than it was.

**Local development needs no Docker.** `hofradar init-db && hofradar serve`
against the venv is the whole loop; the `/opt/hofradar`, `hofradar-update` and
`hofradar.service` commands in `deploy/hetzner/` only exist on a provisioned
box. `scripts/repair_pastes.py` repairs manual pastes stored before #3 in place
(dry run by default) - it re-ingests under the original url so dedupe updates
the row instead of creating a second one.

**Two facts, one German word — check before you reuse „abgelehnt".** The radar's
score gate (`Score.rejected`, per `profile_hash`) and the dossier's triage
verdict (`Property.user_state`) are unrelated and collided in the UI copy until
issue #9. Hiding a property from readers is `user_state="archived"`
(`db/enums.HIDDEN_USER_STATES`), never deletion; `lifecycle.delete_property` is
the narrow, backed-up, cascade-checked exception. See decision 20.

**The Merkliste and filter memory.** `USER_STATES` no longer has a `shortlist`
entry; the reader's bookmarks live in `Property.shortlisted_at` (a timestamp or
null). `POST /property/{id}/merken` is the only route that writes it on a
reader's action; it is also set by the legacy-triage branch (a pre-Merkliste
form still posting `user_state=shortlist`) and by `dedupe.merge` (carrying the
earlier mark across a merge). Filter memory (all sliders
and search parameters) is the `hofradar_radar` cookie plus a `303` redirect from
a bare `/` — no localStorage, no server table, no Javascript. A test must not
assert defaults on `GET /` after it has requested `GET /?...` in the same
`TestClient`, because cookies are preserved and the second request may redirect.
See decision 21.

**Rentals and flats.** A monthly figure is `price_type="rent"`, set by
`parse_price` (price field) or `extract_features.is_rental` (prose), and it is
the one exclusion farm substance cannot override - `REJECT_RENTAL` in scoring,
`rental` in the run log. Flat words live in `keywords.negative` like any other
type. With `TYPESAFE_API_KEY` set, `hofradar.triage` asks Jev the three typed
questions before geocoding and stores the distribution in `evidence["triage"]`;
`triage.decide` is the only reader, used by both the crawl loop and the scoring
engine, and `triage.annotate` the only writer, used by the crawl loop and the
paste box (which never drops a paste - the gate retires it). A known row is
never dropped at the crawl loop - it is ingested so it learns, and the gate
retires it. `scripts/backtest_triage.py` (dry run by default) prints what each
threshold would reject per human verdict and, with `--call --store`, backfills
`evidence["triage"]` on rows the crawl never re-asks about. Decisions 22 and 23.

**PDFs are listings too.** `sources/adapters/_pdfutil.py` is the one PDF
lift (pypdf, core dependency); the Denkmalbörse adapter fetches the exposé
behind every detail page's "zum Exposé" link and merges it (Kurzinfo wins,
PDF fills holes, full text appended), `/add` takes an upload and a pasted PDF
URL, and `lifecycle.ingest` writes a `Document` row per `DocumentRef` so the
dossier links to it. A scan without a text layer is a `warnings` line, never
an empty description. `extract_labeled_fields` reads two facts on one line,
a label above its value (numeric fields only) and a bare "28 Zimmer"; keep
it a string matcher. Tests build PDFs with `tests/fixtures/pdf.py::make_pdf`,
never from real files. Decision 24.

**Unresolved, deliberately.** `config/sources.yaml` gives `manual` role
`primary` while `web/routes/add.py` creates it `LOCAL` with a docstring arguing
it must not be able to mark a listing verified. `init-db` syncs the YAML, so
primary wins in production - which lets the paste box set
`verification_status=verified`. That is invariant 4 territory and needs a
ruling, not a quiet edit to whichever side is easier to change.
