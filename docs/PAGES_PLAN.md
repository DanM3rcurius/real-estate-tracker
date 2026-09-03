# Plan: a GitHub Pages snapshot, a tracked `data/`, and the CI question

Written for a fresh execution session. Nothing in this plan has been built —
the repository is untouched apart from this file.

---

## 1. What was established first (do not re-investigate)

**CI is not broken in this repository.** Every step of `.github/workflows/ci.yml`
was reproduced locally on `d1373e4` and passes:

| Step | Result |
|---|---|
| `ruff check src tests scripts` | All checks passed |
| `python scripts/sync_config_defaults.py` + `git diff --exit-code` | no drift |
| `pytest -q` | 579 passed in 76s |
| Smoke: `init-db`, `config`, `run --dry-run`, import web app | all OK |

All 14 GitHub Actions runs nonetheless failed. The API shows why:

```
job "test": runner_id 0, runner_name "", started 17:10:35, completed 17:10:37
usage:     billable UBUNTU total_ms 0
logs:      HTTP 404 (no log object was ever created)
```

Zero steps recorded, zero billable milliseconds, no runner assigned, no log.
The job died before `Set up job`. **A runner was never allocated**, which is an
account-level Actions block — almost always a billing/spending-limit state —
not a defect in the workflow file. The workflow itself is `state: active`.

**Consequence for this plan:** editing `ci.yml` cannot fix the red runs, and a
Pages workflow will fail exactly the same way until the block is cleared. This
is the one item that needs the repository owner, not code:

- Check <https://github.com/settings/billing> for a spending limit or a failed
  payment method. Actions is free for public repositories, but an account-level
  payment failure can disable it everywhere, public repos included.
- Then check **Settings → Actions → General** on the repo for a policy that
  blocks all workflows.
- A cheap confirmation: once cleared, re-run any existing failed run; if a
  runner is assigned, the steps will pass, because they already pass locally.

**Other established facts:**

- The repository is **public** (`visibility: public`, default branch `trunk`).
- `data/` in a fresh clone is empty. The 232 KB `data/hofradar.sqlite3` present
  after a smoke run holds 0 properties and 10 sources — schema only. **The real
  database is on the owner's host and is not in this repo**, so "commit the data
  folder" cannot mean "commit your listings" from here even if we wanted it to.
- GitHub Pages serves static files only: no Python, no SQLite, and critically
  **no password gate**. `docs/DECISIONS.md` §12 already rules out serverless
  hosts *for the app*; this plan does not contradict it (see §5).

## 2. Decisions taken with the user

Both confirmed before planning:

1. **Pages serves a static snapshot rendered from synthetic demo data.** Not the
   owner's real listings — a public Pages site cannot be password-gated, and
   invariant 8 plus `DECISIONS.md` §11 make a world-readable radar the wrong
   default. The same exporter will run against a real database if the owner ever
   points it at one; only the workflow's data source differs.
2. **`data/` gets committed as structure plus a synthetic seed.** The real
   database, raw scrapes, documents and images stay ignored.

## 3. Work breakdown

### 3.1 `data/` becomes a tracked directory (invariant 1 applies)

Create and commit:

```
data/.gitkeep
data/raw/.gitkeep
data/documents/.gitkeep
data/images/.gitkeep
data/exports/.gitkeep
data/seed/demo_listings.yaml
```

`.gitignore` needs a real edit, not just additions. Today it has:

```
data/raw/
data/documents/
data/images/
data/exports/
```

Git cannot re-include a path inside an excluded **directory**, so `!.gitkeep`
will not work against those lines. Rewrite each as a contents-glob plus an
exception:

```
data/*.sqlite3
data/*.sqlite3-*
data/raw/*
!data/raw/.gitkeep
data/documents/*
!data/documents/.gitkeep
data/images/*
!data/images/.gitkeep
data/exports/*
!data/exports/.gitkeep
```

`data/seed/` is not ignored at all. Verify with `git check-ignore -v` on each
`.gitkeep` and on `data/seed/demo_listings.yaml` — expect no match — and on
`data/hofradar.sqlite3` — expect a match.

### 3.2 `data/seed/demo_listings.yaml` — the synthetic dataset

~12 invented farmsteads. Design rules, all load-bearing:

- **Real Bavarian towns and postcodes, invented everything else.** Miesbach,
  Bad Aibling, Wasserburg am Inn, Holzkirchen, Ebersberg, Bad Tölz, Dorfen,
  Mühldorf, Prien, Grafing, Traunstein, Rosenheim — all inside the 80 km air
  radius of the configured centre (Westham, 47.907/11.840) so the map and the
  distance bands look right. URLs use the `example.invalid` TLD, which is
  reserved by RFC 2606 and can never resolve to a real listing.
- **A header comment stating the file is fictional**, plus a `meta.fictional:
  true` key the exporter reads to drive the snapshot banner.
- **Authored as exposé text, not as typed fields.** Each entry carries
  `title`, `description`, `price_raw` ("645.000 €", "VB 489.000 EUR",
  "Kaufpreis auf Anfrage", "Verkehrswert 720.000 €"), `land_raw`, `living_raw`,
  `year_raw`, `location_raw`. These are fed through `hofradar.normalize` exactly
  like a crawled listing, so property type, feature tags, price type and the
  foreclosure/monument/private-seller booleans in the snapshot are **derived by
  the real code**, not declared in YAML. This makes the demo a genuine test of
  the read path rather than a fixture that bypasses it.
- **Geo supplied per entry, never geocoded.** A `geo:` block with
  `lat`/`lon`/`precision`. Seeding must not touch the network.
- **Some entries deliberately omit `driving_km`.** Invariant 3: unknown road
  distance is `None` and renders as unknown. At least two entries (one `town`
  precision, one `street`) must have no road route, so the snapshot proves the
  distinction is preserved rather than quietly backfilled from air distance.
- Spread across the score range: one over budget (Prien, 1.18 M against a
  750 k purchase target), one foreclosure (Dorfen, "Zwangsversteigerung"), one
  monument (Bad Tölz), a couple of private sellers, one `Grundstück mit
  Altbestand` that should score low on living space.

### 3.3 `src/hofradar/demo.py` — seeding

A single module, not a package; the surface is one function.

```python
def seed_demo(session, profile, *, path: Path | None = None) -> int: ...
```

- Reads the YAML, builds a `RawListing` per entry, calls
  `normalize_listing(raw, keywords)`, builds a `GeoResult` from the `geo:` block
  (computing `distance_air_km` with `hofradar.geo.haversine_km` against the
  profile centre; leaving `distance_driving_km` `None` and `routed=False` when
  the entry omits it), then calls **`hofradar.lifecycle.ingest`**.
- Going through `ingest` is not optional: invariant 1 says it is the only writer
  of `Property` rows and writes the `Observation` first. Do not insert
  `Property` rows directly, and do not add a second writer for the demo path.
- Source: register/reuse a dedicated `demo_seed` source with role `primary` and
  `enabled: false`, added to `config/sources.yaml` with a note saying it exists
  only to attribute the synthetic snapshot rows. Role `primary` is what lets the
  seeded rows carry `verification_status` sensibly; `enabled: false` keeps it
  out of real pipeline runs. **It must never claim `can_prove_absence`** — it
  enumerates nothing (invariant 4b).
- Idempotent: running it twice must yield 12 properties and 24 observations, not
  24 properties. Dedupe already gives this; assert it in a test rather than
  assuming.
- After ingest, call `rescore_all` so the snapshot has scores and cost
  estimates. Scores live in `scores` keyed by `profile_hash` (invariant 5) —
  the exporter must not stash them on `Property`.

CLI: `hofradar seed-demo [--path PATH]`, wired in `build_parser` alongside the
existing subcommands.

### 3.4 `src/hofradar/web/export.py` — the static exporter

Placed inside the existing `web` package rather than as a new top-level package,
because it renders the web app and `docs/MODULE_API.md` governs the
pipeline-stage packages. Public surface, to be added to `MODULE_API.md`:

```python
@dataclass
class ExportResult:
    pages: list[str]
    assets: int
    properties: int

def export_site(destination: Path, *, base_path: str = "",
                session_factory=None, snapshot_note: str | None = None) -> ExportResult: ...
```

**Approach: drive the real app, do not reimplement it.** Build the app with
`create_app(session_factory=...)`, wrap it in Starlette's `TestClient`, and GET
each route in-process. The exported HTML is then byte-for-byte what the server
would have sent, which is the same discipline `radar.py` already applies to its
HTMX partial. No second rendering path to keep in sync.

Routes to capture, written as directory indexes so Pages resolves them:

| Route | Output |
|---|---|
| `/` | `index.html` |
| `/map` | `map/index.html` |
| `/report` | `report/index.html` |
| `/runs` | `runs/index.html` |
| `/property/{public_id}` (each) | `property/<public_id>/index.html` |
| `/api/properties.json` | `api/properties.json` |
| `/api/export.csv` | `api/export.csv` |

Skip `/add` and `/settings` — they are write UIs with nothing to show
statically — and drop them from the exported nav. Keep `/runs`: it is read-only.
Assert every captured response is 200; a 404 or 500 must fail the export loudly
rather than publish a broken page.

Then copy `src/hofradar/web/static/` (including vendored HTMX and Leaflet) to
`static/` in the output. Nothing is fetched from a CDN — `DECISIONS.md` §7.

**Base-path rewriting.** A project Pages site lives at
`/real-estate-tracker/`, so every root-absolute URL breaks. `<base href>` does
not help — it only affects *relative* URLs. Rewrite in the captured HTML:

- `href="/…"`, `src="/…"`, `action="/…"` → prefix with `base_path`.
- Leave protocol-relative and absolute `http(s)://` URLs alone (the OSM tile
  URL in `app.js`, the `example.invalid` listing links).
- Do the rewrite with a targeted regex on the attribute forms above, not a
  blanket string replace of `"/"`.

`app.js` builds one link in JavaScript, in the map popup:

```js
'<a href="/property/' + point.public_id + '">Dossier öffnen</a>'
```

A regex over HTML cannot reach that. Change `app.js` to read a global:

```js
var base = (window.HOFRADAR_BASE || "");
… '<a href="' + base + '/property/' + point.public_id + '">Dossier öffnen</a>'
```

and have the exporter inject `<script>window.HOFRADAR_BASE="…";</script>` into
each page's `<head>`. With no global set the expression is `""` and the live
app behaves exactly as it does today — this is why the change is safe to make
in the shipped asset rather than in an export-only copy.

**Neutralising the interactive surface.** On a static host the HTMX slider form
would fire `GET /api/results` and fail. Strip `hx-get`/`hx-post`/`hx-target`/
`hx-trigger`/`hx-swap` attributes from the captured HTML and remove the triage
`<form method="post">` blocks and the "Jetzt suchen" run button.

Deliberately **keep the sliders themselves rendered and live**: `updateDerived`
in `app.js` is pure client-side arithmetic mirroring `BudgetConfig.effective_*`,
so dragging still updates the derived driving bands and purchase bands. The
result list below stays fixed. That is the honest demo — the thing the product
*is* still moves, and nothing pretends to re-query.

**The banner.** Inject a fixed notice into `<main>` on every page: German UI
copy (per the conventions), stating this is a static snapshot of invented data,
naming the export timestamp, and linking to the repository. Non-negotiable —
a public page showing farmstead listings must not be mistakable for real market
data.

CLI: `hofradar export-site --out site/ [--base-path /real-estate-tracker]`.

### 3.5 `.github/workflows/pages.yml`

```yaml
name: Pages
on:
  push:
    branches: ["trunk"]
  workflow_dispatch:
permissions:
  contents: read
  pages: write
  id-token: write
concurrency:
  group: pages
  cancel-in-progress: false
```

Two jobs. **build**: checkout, `setup-python` 3.12 with pip cache, `pip install
-e ".[dev,pdf,images]"`, then

```bash
hofradar init-db
hofradar seed-demo
hofradar export-site --out site --base-path /real-estate-tracker
```

with `HOFRADAR_DATA_DIR` pointed at a scratch directory so the build never
depends on a committed database, and **no** `HOFRADAR_PASSWORD*` set (a gate on
the export would just produce login pages). Note `HOFRADAR_OFFLINE=1` is
appropriate *here* — unlike the test suite, where CLAUDE.md forbids it — because
seeding and export must make no outbound calls at all. Upload with
`actions/upload-pages-artifact@v3`. **deploy**: `actions/deploy-pages@v4` with
`environment: github-pages`.

Repository setting the owner must flip once, by hand:
**Settings → Pages → Source: GitHub Actions**. The workflow cannot set this
itself, and the first run fails with a clear error until it is set.

### 3.6 Tests (`tests/web/test_export_site.py`, `tests/test_demo_seed.py`)

Mirroring `tests/` to `src/`, using the existing in-memory `db_session` and
factory fixtures. Must cover:

- Seeding twice yields 12 properties and 24 observations — idempotent, and
  observations are append-only.
- Seeding makes no network call: assert under `respx` with all routes
  unmocked, so any outbound request raises.
- A seed entry with no `driving_km` produces `distance_driving_km is None`, and
  its exported dossier renders the unknown marker rather than the air figure.
  This is invariant 3 and is the test most worth having.
- `export_site` writes `index.html`, `map/index.html`, `report/index.html` and
  one directory per property; every capture was 200.
- **No root-absolute URL survives**: grep every exported HTML file for
  `href="/`, `src="/` and `action="/` and assert zero matches when a base path
  was given.
- No `hx-get`/`hx-post` attribute survives in the exported HTML.
- The banner text appears on every exported page.
- The vendored Leaflet and HTMX assets landed in the output.

### 3.7 Documentation

- `docs/DECISIONS.md` **§13 — "Pages hosts a read-only snapshot, not the app."**
  This must be written so it reads as a companion to §12, not a reversal of it:
  the app still needs a disk and a long-running process; what Pages gets is a
  generated artifact with no database behind it. Record the consequence that the
  snapshot is public and therefore synthetic.
- `docs/DEPLOY.md`: a section for the snapshot, and a row in the table at the
  top — Pages: "✅ for a read-only demo, ❌ for the app" — so the table stops
  implying every static host is simply wrong.
- `README.md`: link the live snapshot.
- `CLAUDE.md`: add `demo.py` and `web/export.py` to the Layout block; add
  `data/seed/` too.
- `docs/MODULE_API.md`: the `export_site` surface.
- Delete this plan file in the same commit that completes the work, or fold it
  into `DEPLOY.md` — it should not outlive its usefulness.

## 4. Suggested commit sequence

1. `data/` structure, `.gitignore` rewrite, seed YAML, `demo_seed` source entry.
2. `hofradar.demo` + `seed-demo` CLI + seed tests.
3. `app.js` base-path global (behaviour-neutral on its own).
4. `web/export.py` + `export-site` CLI + export tests.
5. `pages.yml`.
6. Docs.

Run `python scripts/sync_config_defaults.py` after step 1 — `config/sources.yaml`
changes and CI fails on drift. Run `ruff check src tests scripts` and the full
suite before each push.

## 5. Risks and things that are not being done

- **The Pages deploy will fail until Actions runners are unblocked** (§1). The
  workflow can be written and merged, but nothing will go live before the owner
  resolves the account-level state. Do not spend a cycle "fixing" `pages.yml`
  when it fails identically to `ci.yml` — check `runner_id` first.
- **The snapshot is a point-in-time build**, not a live view. It refreshes only
  when `trunk` is pushed, or manually via `workflow_dispatch`. Wiring it to a
  schedule would only re-render the same synthetic data, so it is not proposed.
- **Nothing here publishes the owner's real listings**, and no code path is
  added that could start doing so by accident: the workflow seeds a throwaway
  database from the YAML and never reads a committed `.sqlite3`.
- **No change to source adapters, scoring, or the crawl.** The exporter is a
  read-only consumer of the web layer.
- **`can_prove_absence` stays false for the demo source.** A seed file is
  exactly the "paste box / CSV import" case in invariant 4b: its silence proves
  nothing.
