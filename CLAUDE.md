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
pytest -q                         # exactly what CI runs, no PYTHONPATH needed
ruff check src tests scripts      # CI lints scripts/ too
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

**CI runs, and is green.** Runs 1-44 really did die in 2-4 seconds without
reaching a runner; that ended at run 45 (2026-09-04) and every run since
executes its steps. Do not read a red check as infrastructure without opening
the run.

Two real defects were hiding behind each other, because a failed step skips the
rest - so the tests and both smoke steps had never executed in CI at all until
they were fixed (`a0fd490`, `cd5bd77`, `e95403c`):

1. *Config defaults are in sync* failed on drift `scripts/sync_config_defaults.py`
   could not stage: it copied with `copy2`, which carries the source mtime
   across, and `2000` -> `1000` keeps the byte length, so git's index saw the
   same size and mtime and `git add` staged nothing. It copies without metadata
   now. The guard itself was right - the packaged copy is what an installed
   wheel reads, which is every container deployment.
2. *Test* then failed collection outright: four modules import their sibling
   conftest absolutely (`from tests.web.conftest import ...`), which needs the
   repo root on `sys.path`. `python -m pytest` adds the working directory and
   the plain `pytest` CI runs does not, so the documented local command was the
   one invocation that could not fail. The root is in `pythonpath` now. Keep the
   documented command and CI's command identical.

Two traps worth keeping in mind:

- **`hofradar run --dry-run` is not a dry run of the crawl.** `dry_run` only
  skips the writes, so discovery and fetching still hit the live portals. The
  smoke step passes `--sources manual`, which enumerates nothing and still walks
  every stage. Do not widen it back without deciding that CI should crawl the
  real web on every push.
- **A fixed test clock and a wall-clock function make a time bomb.** The scoring
  fixtures are dated against `tests/scoring/conftest.py`'s frozen
  `2026-09-03`, and `rescore_all` scored against the wall clock, so a property
  aged past a freshness band and the confidence gate dropped it out of the
  ranking - a test that passed for eleven days and then could not. `rescore_all`
  takes `now` for this reason; pass it whenever the answer must not depend on
  what day it is.

**Check `origin/trunk` before diagnosing anything.** Several Claude sessions
work this repo in parallel and branches sit unmerged for weeks. Both CI defects
above were diagnosed and fixed twice, independently, because the second session
reasoned from a stale `origin/trunk` ref rather than fetching first. `git fetch
origin trunk` costs nothing; re-solving a solved problem and then resolving the
merge conflicts costs a session.

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

**A title has two owners.** `canonical_title` is the listing's and `ingest`
rewrites it; `user_title` is the reader's, written only by
`POST /property/{id}/title` (and carried by `dedupe.merge`), never by `ingest`.
Render `display_title` (`user_title or canonical_title`) wherever a reader sees
a title; score, dedupe and feed the LLM `canonical_title`. Decision 29.

**A Sanierungsstufe has two owners too.** `costmodel.listing_renovation_tier`
is the inference from tags, condition and building age; a reader who has
walked the building can pin their own in `Property.user_renovation_tier`
(`POST /property/{id}/sanierungsstufe`, never written by `ingest`), and
`infer_renovation_tier` - what `estimate_costs` prices - prefers it outright,
no age bump. It also gates: a reader's tier counts as stood-behind evidence
(`costmodel.STATED_EVIDENCE`), so it may hard-reject on total cost where a
guessed one only flags. `scoring.rescore_property` recomputes cost and score
for one property in the same transaction as the edit. Decision 30.

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
never from real files. Decision 24. A letter above Latin-1 inside a word
("WohnŦäche") is a ligature glyph the font never mapped, and
`recover_ligatures` reads it back by stem. A `location_raw` has to read like a
place, so a "Lage:" paragraph never becomes the town. Decision 28. "Verkauf" is
a price label that is as often a value, so it claims only a price-shaped value
even behind a colon; a cover that opens with its fact box ("Baujahr 1993", ...)
is skipped as a block by `pdf_title`. Decision 29.

**A link is only a link when a browser can follow it.** `upload:<digest>`
(a reader's PDF) and `manual:<timestamp>` (a text paste) are how a hand-added
listing is *named*; they are not addresses, and putting one in an `href`
produces a button that does nothing when clicked - which is how the dossier's
"Inserat öffnen", its per-fact "Quelle", the Quellen list and the Dokument link
all behaved until decision 25. `web/query.is_web_url` is the one answer, also
registered as the Jinja test `{% if url is web_url %}`; `open_link` decides
what the header button points at and `no_link_reason` says why when it points
at nothing. `best_url` still returns the identity - the JSON, the CSV and the
digest quote it. The uploaded file is served by `GET /document/{id}` out of
`web/uploads.uploads_dir()`, and that module - not `routes/add.py` - now owns
where uploads live.

**A mark is not a score, and the Merkliste must never wait for one.**
`scoring.ranked_properties` joins `Score` on the live `profile_hash`, so a
property nothing has scored under it is not in the ranking. `/add` stores but
never scores, and a rescore that cannot write (a crawl holding the lock) leaves
rows unscored for longer - so hand-added properties vanished from the Merkliste
under "Alle gemerkten Objekte sind archiviert", a cause the page had never
checked. `web/query._marked_pairs` now loads the marked, unmerged set from the
table and merges it into the ranking; an unscored card says "noch nicht
bewertet". `/merken` follows `merged_into_id` the way `ingest` does, because a
merged row is never rendered. When you touch `build_results`, the rule is: the
Merkliste's rows come from `properties`, never only from the scorer.

**Unresolved, deliberately.** `config/sources.yaml` gives `manual` role
`primary` while `web/routes/add.py` creates it `LOCAL` with a docstring arguing
it must not be able to mark a listing verified. `init-db` syncs the YAML, so
primary wins in production - which lets the paste box set
`verification_status=verified`. That is invariant 4 territory and needs a
ruling, not a quiet edit to whichever side is easier to change.
